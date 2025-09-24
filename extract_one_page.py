#!/usr/bin/env python3
"""
extract_harililamrut_onepage.py

Single-page extractor (no robots check).
- Removes top "Kalash X / Vishram Y" heading if present.
- Removes the bottom navigation table (the <table style="margin:auto"> block linking to other sections).
- Replaces inline footnote links with plain bracketed numbers like [1].
- Extracts footnotes from <div id="footnotes" class="footnotes"> and appends them under "## Footnotes".
- Converts cleaned HTML to Markdown and writes to output file.

Usage:
  python extract_harililamrut_onepage.py "<PAGE_URL>" [output.md]

Dependencies:
  pip install requests beautifulsoup4 lxml markdownify
"""
import sys
import re
import time
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup, NavigableString
from markdownify import markdownify as md

# ---------- CONFIG ----------
USER_AGENT = ("Mozilla/5.0 (compatible; HarililamrutOnePage/1.0; +https://github.com/yourname) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
REQUEST_TIMEOUT = 25
# ----------------------------

NAV_LINK_PATTERNS = [
    "/vachanamrut/", "/vato/", "/kirtan/", "/kavya/",
    "/aksharamrutam/", "/chintamani/"
]

def fetch_html(url):
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.content

def remove_top_kalash_heading(soup):
    """
    Remove visible headings like "Kalash 1 / Vishram 1" (any heading matching that pattern).
    Case-insensitive.
    """
    pattern = re.compile(r"kalash\s*\d+\s*/\s*vishram\s*\d+", re.I)
    for htag in ("h1","h2","h3","h4"):
        for h in soup.find_all(htag):
            text = h.get_text(" ", strip=True)
            if text and pattern.search(text):
                h.decompose()
                return True
    return False

def remove_nav_table(soup):
    """
    Remove the navigation table with style 'margin:auto' and/or that contains the known nav links.
    """
    removed = False
    for table in list(soup.find_all("table")):
        # Check style contains margin:auto (ignore whitespace/case)
        style = (table.get("style") or "").replace(" ", "").lower()
        if "margin:auto" in style:
            table.decompose()
            removed = True
            continue

        # Otherwise check anchors inside the table for known nav hrefs
        anchors = table.find_all("a")
        for a in anchors:
            href = a.get("href") or ""
            # Normalize and check if any known nav substring is present
            if any(p in href for p in NAV_LINK_PATTERNS):
                table.decompose()
                removed = True
                break
    return removed

def extract_footnotes(soup):
    """
    Extract footnotes from <div id="footnotes" ...> (or class 'footnotes').
    Return a list of dicts: [{'id': id_or_generated, 'num': n, 'html': inner_html}, ...]
    Remove the footnotes div from the soup.
    """
    footnotes_div = soup.find("div", id="footnotes") or soup.find("div", class_=lambda v: v and "footnotes" in v)
    results = []
    if not footnotes_div:
        return results

    # Prefer enumerating <li> items if present
    ol = footnotes_div.find("ol")
    if ol:
        items = ol.find_all("li", recursive=False)
        for i, li in enumerate(items, start=1):
            fid = li.get("id") or f"fn-{i}"
            # inner HTML of li
            inner = "".join(str(c) for c in li.contents).strip()
            results.append({"id": fid, "num": i, "html": inner})
    else:
        # fallback: find direct children that look like footnote entries (li/div/p)
        candidates = []
        for child in footnotes_div.find_all(recursive=False):
            if child.name in ("li","div","p"):
                candidates.append(child)
        if candidates:
            for i, el in enumerate(candidates, start=1):
                fid = el.get("id") or f"fn-{i}"
                inner = "".join(str(c) for c in el.contents).strip()
                results.append({"id": fid, "num": i, "html": inner})
        else:
            # last fallback: take entire footnotes_div as a single footnote
            inner = "".join(str(c) for c in footnotes_div.contents).strip()
            results.append({"id": footnotes_div.get("id") or "fn-1", "num": 1, "html": inner})

    # remove footnotes div so it is not duplicated in main content
    footnotes_div.decompose()
    return results

def find_and_replace_inline_refs(soup, footnote_entries):
    """
    Replace inline anchor references to footnotes with plain bracketed numbers like [1].
    We consider anchors with href starting with '#' and target id matching footnote_entries' ids
    OR anchor text that's purely digits and links to '#fn...' style.
    """
    id_to_num = {entry["id"]: entry["num"] for entry in footnote_entries}
    # also accept variants without 'fn' prefix: build pattern of numbers present
    known_nums = set(entry["num"] for entry in footnote_entries)

    # find anchors that point to an ID in our footnotes or that look like footnote refs
    anchors = list(soup.find_all("a"))
    for a in anchors:
        href = a.get("href") or ""
        text = a.get_text("", strip=True) or ""
        replaced = False

        if href.startswith("#"):
            target = href[1:]
            # direct ID match
            if target in id_to_num:
                num = id_to_num[target]
                new_node = NavigableString(f"[{text or num}]")
                a.replace_with(new_node)
                replaced = True
            else:
                # sometimes footnote refs use fn1 or fn-1, try extract digits
                m = re.search(r"(\d+)", target)
                if m:
                    num = int(m.group(1))
                    if num in known_nums:
                        new_node = NavigableString(f"[{text or num}]")
                        a.replace_with(new_node)
                        replaced = True

        # If href contains 'javascript' or links back to footnote ref text, but no '#', try text-only numeric anchors
        if not replaced and text.isdigit():
            # if anchor text is numeric and it's likely a footnote ref, replace
            # We conservatively replace only if the anchor is small (no child tags) and short text
            if len(text) <= 4:
                new_node = NavigableString(f"[{text}]")
                a.replace_with(new_node)
                replaced = True

    return

def convert_and_write(soup, footnote_entries, output_file):
    """
    Convert the cleaned soup to markdown, then append footnotes in the requested format
    (## Footnotes, then "[1] footnote text" entries). Write to output_file (overwrite).
    """
    # Heuristic: find main content (prefer <main>, <article>, else big candidate)
    content_candidate = None
    if soup.find("main"):
        content_candidate = soup.find("main")
    elif soup.find("article"):
        content_candidate = soup.find("article")
    else:
        # find large candidate by id/class or fallback to body
        candidates = []
        for attr in ("id","class"):
            for name in ("content","main","page","article","container","wrapper","post","entry"):
                found = soup.find(attrs={attr: lambda v: v and name in v})
                if found and found.get_text(strip=True):
                    candidates.append(found)
        if candidates:
            content_candidate = max(candidates, key=lambda t: len(t.get_text(" ", strip=True)))
        else:
            content_candidate = soup.body or soup

    # Convert to markdown
    html_content = str(content_candidate)
    md_main = md(html_content, heading_style="ATX").strip()

    # Build footnotes markdown (per-page)
    foot_md = ""
    if footnote_entries:
        footlist_parts = []
        for entry in footnote_entries:
            # convert entry['html'] to markdown (strip potential leading numbers like "1." if present)
            converted = md(entry["html"], heading_style="ATX").strip()
            converted = re.sub(r'^\s*\d+\.\s*', '', converted)  # remove leading "1. " if any
            footlist_parts.append(f"[{entry['num']}] {converted}")
        foot_md = "\n\n## Footnotes\n\n" + "\n\n".join(footlist_parts)

    # Remove the top page header that the previous scripts inserted (if present in MD),
    # specifically a line like "## Kalash 1 / Vishram 1" at the very start.
    # But since we operate on HTML before conversion, we've already removed h-tags; still do a safety cleanup:
    md_main = re.sub(r'^(#{1,6}\s*Kalash\s*\d+\s*/\s*Vishram\s*\d+\s*\n)+', '', md_main, flags=re.I)

    # Final combined text
    combined = md_main.strip()
    if foot_md:
        combined = combined + "\n\n" + foot_md

    # Tidy up extra blank lines
    combined = re.sub(r'\n{3,}', '\n\n', combined)

    # Write file
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(combined)
    print("Wrote:", output_file)

def process_one_page(url, output_file):
    print("Fetching:", url)
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    # Remove top Kalash/Vishram heading if present
    removed_top = remove_top_kalash_heading(soup)
    if removed_top:
        print("Removed top Kalash/Vishram heading")

    # Remove the nav table if present
    removed_table = remove_nav_table(soup)
    if removed_table:
        print("Removed bottom navigation table")

    # Extract footnotes and remove the footnotes div
    footnotes = extract_footnotes(soup)
    if footnotes:
        print(f"Found {len(footnotes)} footnotes")

    # Replace inline footnote links with [n]
    find_and_replace_inline_refs(soup, footnotes)

    # Convert to markdown and write
    convert_and_write(soup, footnotes, output_file)

def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_harililamrut_onepage.py <URL> [output.md]")
        sys.exit(1)
    url = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "page_harililamrut.md"
    process_one_page(url, out)

if __name__ == "__main__":
    main()
