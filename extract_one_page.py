#!/usr/bin/env python3
"""
extract_harililamrut_onepage_v2.py

Improved one-page extractor for Harililamrut (no robots check).
- Removes huge navigation blocks & bottom nav table
- Replaces inline footnote links with [n]
- Extracts footnotes from <div id="footnotes" ...> and outputs per-page footnotes
- Converts to Markdown.

Usage:
  python extract_harililamrut_onepage_v2.py "<URL>" [output.md]
"""
import sys, re, time
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup, NavigableString
from markdownify import markdownify as md

# -------- CONFIG ----------
USER_AGENT = ("Mozilla/5.0 (compatible; HarililamrutOnePageV2/1.0; +https://github.com/you) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
REQUEST_TIMEOUT = 25
NAV_KEYWORDS = ["વિશ્રામ", "કળશ", "Kalash", "Vishram"]  # keywords often in the big nav block
MAX_LINKS_IN_BLOCK = 12    # block with more than this many small links is likely a nav block
# --------------------------

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
    pattern = re.compile(r"kalash\s*\d+\s*/\s*vishram\s*\d+", re.I)
    for htag in ("h1","h2","h3","h4"):
        for h in soup.find_all(htag):
            text = h.get_text(" ", strip=True)
            if text and pattern.search(text):
                h.decompose()
                return True
    return False

def remove_nav_table(soup):
    removed = False
    for table in list(soup.find_all("table")):
        style = (table.get("style") or "").replace(" ", "").lower()
        if "margin:auto" in style:
            table.decompose()
            removed = True
            continue
        anchors = table.find_all("a")
        for a in anchors:
            href = a.get("href") or ""
            if any(p in href for p in NAV_LINK_PATTERNS):
                table.decompose()
                removed = True
                break
    return removed

def remove_large_nav_blocks(soup):
    """
    Remove blocks that look like large navigation lists:
    - contain many <a> tags (over MAX_LINKS_IN_BLOCK),
    - or contain repeated NAV_KEYWORDS occurrences
    We target <div>, <nav>, <aside>, or big <ul> containers.
    """
    removed_any = False
    candidates = soup.find_all(['div', 'nav', 'aside', 'ul', 'section'], recursive=True)
    for el in candidates:
        anchors = el.find_all('a')
        if len(anchors) >= MAX_LINKS_IN_BLOCK:
            # Check average anchor text length (nav links tend to be short)
            avg_len = sum(len(a.get_text(strip=True)) for a in anchors) / max(1, len(anchors))
            short_links = sum(1 for a in anchors if len(a.get_text(strip=True)) < 40)
            if short_links >= max(10, int(0.8 * len(anchors))) or avg_len < 40:
                # also avoid removing main content accidentally: ensure links are mostly internal
                internal = sum(1 for a in anchors if (a.get('href') or "").startswith("index.php") or (a.get('href') or "").startswith("/"))
                if internal >= max(5, int(0.5 * len(anchors))):
                    el.decompose()
                    removed_any = True
                    continue
        # check for repeated keywords in text
        txt = el.get_text(" ", strip=True)
        keyword_hits = sum(txt.count(k) for k in NAV_KEYWORDS)
        if keyword_hits >= 6:  # heuristic threshold
            el.decompose()
            removed_any = True
    return removed_any

def extract_footnotes(soup):
    """
    Extract footnotes from <div id="footnotes" ...> or class contains 'footnotes'.
    Return list of dicts [{'id': id, 'num': n, 'html': cleaned_html, 'text': fallback_text}, ...]
    """
    footnotes_div = soup.find("div", id="footnotes") or soup.find("div", class_=lambda v: v and "footnotes" in v)
    if not footnotes_div:
        return []

    results = []
    # Look for ordered list items inside
    ol = footnotes_div.find("ol")
    if ol:
        items = ol.find_all("li", recursive=False)
        for i, li in enumerate(items, start=1):
            # remove backrefs and sup/backlink anchors inside the footnote
            for back in li.find_all('a'):
                href = (back.get('href') or "")
                cls = " ".join(back.get('class') or [])
                if href.startswith("#") or 'back' in cls.lower() or 'reverse' in cls.lower() or 'fnref' in cls.lower():
                    back.decompose()
            for sup in li.find_all('sup'):
                # if sup only contains a small anchor or number, drop it
                if not sup.get_text(strip=True):
                    sup.decompose()
                else:
                    # if sup contains backlink, remove
                    if sup.find('a'):
                        sup.decompose()

            inner_html = "".join(str(c) for c in li.contents).strip()
            # convert to markdown; if empty, fallback to plain text
            converted = md(inner_html, heading_style="ATX").strip()
            if not converted:
                converted = li.get_text(" ", strip=True)
            results.append({"id": li.get('id') or f"fn-{i}", "num": i, "html": inner_html, "md": converted})
    else:
        # Try to detect child blocks inside footnotes_div
        children = [c for c in footnotes_div.find_all(recursive=False) if getattr(c, 'name', None) in ('li','div','p')]
        if children:
            for i, el in enumerate(children, start=1):
                for back in el.find_all('a'):
                    href = (back.get('href') or "")
                    cls = " ".join(back.get('class') or [])
                    if href.startswith("#") or 'back' in cls.lower() or 'reverse' in cls.lower() or 'fnref' in cls.lower():
                        back.decompose()
                inner_html = "".join(str(c) for c in el.contents).strip()
                converted = md(inner_html, heading_style="ATX").strip()
                if not converted:
                    converted = el.get_text(" ", strip=True)
                results.append({"id": el.get('id') or f"fn-{i}", "num": i, "html": inner_html, "md": converted})
        else:
            # Fallback: treat entire div as single footnote
            inner_html = "".join(str(c) for c in footnotes_div.contents).strip()
            converted = md(inner_html, heading_style="ATX").strip()
            if not converted:
                converted = footnotes_div.get_text(" ", strip=True)
            results.append({"id": footnotes_div.get('id') or "fn-1", "num": 1, "html": inner_html, "md": converted})

    # remove footnotes div so it doesn't appear in main content
    footnotes_div.decompose()
    return results

def replace_inline_footnote_refs(soup, footnotes):
    """
    Replace inline anchors/sup that link to footnotes with plain [n].
    Handles:
      <a href="#fn1">1</a>, <sup><a href="#fn1">1</a></sup>, <a class="fnref" href="#...">, etc.
    """
    id_to_num = {entry['id']: entry['num'] for entry in footnotes}
    known_nums = set(entry['num'] for entry in footnotes)

    # Find anchors that look like footnote refs
    for a in list(soup.find_all('a')):
        href = (a.get('href') or "")
        text = a.get_text("", strip=True)
        cls = " ".join(a.get('class') or [])
        replaced = False

        if href.startswith("#"):
            target = href[1:]
            # direct id match
            if target in id_to_num:
                num = id_to_num[target]
                a.replace_with(NavigableString(f"[{num}]"))
                replaced = True
            else:
                # try digits in target
                m = re.search(r'(\d+)', target)
                if m:
                    n = int(m.group(1))
                    if n in known_nums:
                        a.replace_with(NavigableString(f"[{n}]"))
                        replaced = True

        # If class indicates footnote reference or text is pure digit, conservatively replace
        if not replaced and (re.search(r'fnref|footnote', cls, re.I) or text.isdigit()):
            # ensure small anchor (not a long link)
            if len(text) <= 6:
                a.replace_with(NavigableString(f"[{text}]"))
                replaced = True

    # Also replace standalone <sup> that contain numbers (and no other content)
    for s in list(soup.find_all('sup')):
        txt = s.get_text("", strip=True)
        if txt.isdigit() and len(txt) <= 4:
            s.replace_with(NavigableString(f"[{txt}]"))

def find_main_content(soup):
    if soup.find("main"):
        return soup.find("main")
    if soup.find("article"):
        return soup.find("article")
    # fallback to large candidate
    candidates = []
    for attr in ("id","class"):
        for name in ("content","main","page","article","container","wrapper","post","entry"):
            found = soup.find(attrs={attr: lambda v: v and name in v})
            if found and found.get_text(strip=True):
                candidates.append(found)
    if candidates:
        return max(candidates, key=lambda t: len(t.get_text(" ", strip=True)))
    return soup.body or soup

def convert_and_write(soup, footnotes, output_file):
    main = find_main_content(soup)
    # remove nav/header/footer/aside in case present
    for t in main.find_all(["nav","header","footer","aside"]):
        t.decompose()

    html_content = str(main)
    md_main = md(html_content, heading_style="ATX").strip()

    # remove accidental leading "## Kalash / Vishram" headings produced earlier
    md_main = re.sub(r'^(#{1,6}\s*Kalash\s*\d+\s*/\s*Vishram\s*\d+\s*\n)+', '', md_main, flags=re.I)

    foot_md = ""
    if footnotes:
        parts = []
        for e in footnotes:
            txt = e.get('md') or e.get('html') or ''
            txt = txt.strip()
            # Clean up text: remove leading numbering like "1." if present
            txt = re.sub(r'^\s*\d+\.\s*', '', txt)
            parts.append(f"[{e['num']}] {txt}")
        foot_md = "\n\n## Footnotes\n\n" + "\n\n".join(parts)

    combined = md_main
    if foot_md:
        combined = combined + "\n\n" + foot_md
    combined = re.sub(r'\n{3,}', '\n\n', combined)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(combined)
    print("Wrote:", output_file)

def process_one_page(url, output_file):
    print("Fetching:", url)
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    # Remove top Kalash/Vishram heading if present
    if remove_top_kalash_heading(soup):
        print("Removed top Kalash/Vishram heading")

    # More aggressive nav removals
    if remove_nav_table(soup):
        print("Removed bottom nav table (style/nav links)")
    if remove_large_nav_blocks(soup):
        print("Removed large navigation blocks (many small links)")

    # Extract footnotes and remove the footnotes div
    footnotes = extract_footnotes(soup)
    if footnotes:
        print(f"Extracted {len(footnotes)} footnotes")

    # Replace inline refs like <a href="#fn1">1</a> or <sup><a>1</a></sup> with [1]
    replace_inline_footnote_refs(soup, footnotes)

    # Final convert and write file
    convert_and_write(soup, footnotes, output_file)

def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_harililamrut_onepage_v2.py <URL> [output.md]")
        sys.exit(1)
    url = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "page_harililamrut.md"
    process_one_page(url, out)

if __name__ == "__main__":
    main()
