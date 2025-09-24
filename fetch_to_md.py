#!/usr/bin/env python3
"""
extract_harililamrut_singlefile.py

Fetch the entry Harililamrut page and follow <a class="nav_right"> repeatedly,
extract content and footnotes (exclude <title> and header <h1> with the header image),
and write all content into ONE Markdown file.

Usage:
  python extract_harililamrut_singlefile.py "<START_URL>" [output.md]

Important:
  - This script intentionally DOES NOT check robots.txt.
  - Be responsible and use only with permission.

Dependencies:
  pip install requests beautifulsoup4 lxml markdownify
"""

import sys
import time
import re
from urllib.parse import urljoin, urlparse, parse_qs
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# -------------------------
# Config
# -------------------------
USER_AGENT = (
    "Mozilla/5.0 (compatible; ExtractHarililamrut/1.0; +https://github.com/yourname) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
REQUEST_TIMEOUT = 25
REQUEST_DELAY = 0.8          # seconds between requests (polite small delay)
MAX_PAGES = 200              # safety cap to avoid infinite loops
OUTPUT_FILE_DEFAULT = "all_harililamrut.md"
# -------------------------

def fetch_html(url):
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.content

def clean_and_extract_parts(soup):
    """
    Remove <title> and header <h1> with header image, strip scripts/styles etc.
    Return tuple: (content_html, footnotes_html, visible_title_text)
    """
    # Remove <title>
    if soup.title:
        soup.title.decompose()

    # Remove <h1> containing header image (harililamrut-header.jpg)
    for h1 in soup.find_all("h1"):
        img = h1.find("img")
        if img and "harililamrut-header" in (img.get("src") or ""):
            h1.decompose()

    # Remove scripts, styles, noscript, iframe, form, input, button, svg
    for tagname in ("script", "style", "noscript", "iframe", "form", "input", "button", "svg"):
        for t in soup.find_all(tagname):
            t.decompose()

    # Extract footnotes div if present, then remove it to avoid duplication
    footnotes_div = soup.find("div", id="footnotes")
    footnotes_html = ""
    if footnotes_div:
        footnotes_html = str(footnotes_div)
        footnotes_div.decompose()

    # Remove nav/header/footer/aside; but nav_right is typically an <a> we capture before calling this if needed
    for t in soup.find_all(["nav", "header", "footer", "aside"]):
        t.decompose()

    # Determine main content: prefer <main>, <article>, or largest candidate
    content_candidate = None
    if soup.find("main"):
        content_candidate = soup.find("main")
    elif soup.find("article"):
        content_candidate = soup.find("article")
    else:
        candidates = []
        for attr in ("id", "class"):
            for name in ("content", "main", "page", "article", "container", "wrapper", "post", "entry"):
                found = soup.find(attrs={attr: lambda v: v and name in v})
                if found and found.get_text(strip=True):
                    candidates.append(found)
        if candidates:
            content_candidate = max(candidates, key=lambda t: len(t.get_text(" ", strip=True)))
        else:
            content_candidate = soup.body or soup

    # Clean sidebars inside content
    for side in content_candidate.find_all(attrs={"class": lambda v: v and ("sidebar" in v or "nav" in v)}):
        side.decompose()

    # Optional visible title (e.g., first H1 inside content)
    visible_title = None
    h1 = content_candidate.find("h1")
    if h1 and h1.get_text(strip=True):
        visible_title = h1.get_text(strip=True)

    return str(content_candidate), footnotes_html, visible_title

def html_to_markdown_for_page(content_html, footnotes_html, page_label):
    """
    Convert content and footnotes HTML to markdown for a single page.
    Returns: markdown_text (string), footnotes_markdown (string)
    """
    md_main = md(content_html, heading_style="ATX")
    md_main = md_main.strip()
    md_footnotes = ""
    if footnotes_html and footnotes_html.strip():
        md_footnotes = md(footnotes_html, heading_style="ATX").strip()
    # Prepare full page block with heading to separate pages
    page_header = f"\n\n---\n\n## {page_label}\n\n"
    return page_header + md_main, md_footnotes

def find_next_link(soup, base_url):
    """
    Find the anchor with class containing 'nav_right' and return absolute URL (or None)
    """
    a = soup.find("a", class_=lambda v: v and "nav_right" in v)
    if a and a.get("href"):
        return urljoin(base_url, a.get("href"))
    return None

def make_page_label(url, visible_title=None):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    kalash = qs.get("kalash", [None])[0]
    vishram = qs.get("vishram", [None])[0]
    if kalash and vishram:
        base = f"Kalash {kalash} / Vishram {vishram}"
    else:
        base = parsed.path.strip("/") or url
    if visible_title:
        return f"{base} — {visible_title}"
    return base

def run_all(start_url, output_file=OUTPUT_FILE_DEFAULT, delay=REQUEST_DELAY, max_pages=MAX_PAGES):
    visited = set()
    current = start_url
    all_md_parts = []
    collected_footnotes = []   # list of tuples (page_label, md_footnotes)
    page_count = 0

    while current and page_count < max_pages:
        if current in visited:
            print("Already visited, stopping to avoid loop:", current)
            break
        print(f"Fetching ({page_count+1}): {current}")
        try:
            raw = fetch_html(current)
        except Exception as e:
            print("Error fetching:", e)
            break

        soup = BeautifulSoup(raw, "lxml")

        # Capture next link before we strip nav/header
        next_link = find_next_link(soup, current)

        # Clean & extract content/footnotes
        content_html, footnotes_html, visible_title = clean_and_extract_parts(soup)
        page_label = make_page_label(current, visible_title)

        page_md, md_footnotes = html_to_markdown_for_page(content_html, footnotes_html, page_label)
        all_md_parts.append(page_md)
        if md_footnotes:
            collected_footnotes.append((page_label, md_footnotes))

        visited.add(current)
        page_count += 1

        # polite delay
        if delay and page_count < max_pages:
            time.sleep(delay)

        # Move to next page if present
        if next_link:
            # Normalize next_link (remove fragment)
            parsed_next = urlparse(next_link)
            next_link = parsed_next._replace(fragment="").geturl()
            if next_link in visited:
                print("Next link already visited; stopping.")
                break
            current = next_link
        else:
            print("No next link found; finishing.")
            current = None

    # Combine all parts into single markdown text
    combined_md = "# Harililamrut — Combined Extract\n\n"
    combined_md += "\n\n".join(part for part in all_md_parts)

    # Append combined footnotes at the end, grouped by page
    if collected_footnotes:
        combined_md += "\n\n---\n\n## Footnotes (combined)\n\n"
        for page_label, md_footnotes in collected_footnotes:
            combined_md += f"### {page_label}\n\n"
            # md_footnotes may already include headings; ensure separation
            combined_md += md_footnotes + "\n\n"

    # Final tidy: collapse 3+ blank lines
    combined_md = re.sub(r"\n{3,}", "\n\n", combined_md)

    # Write output
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(combined_md)
    print(f"Wrote combined Markdown to: {output_file} (pages fetched: {page_count})")

# -------------------------
# CLI
# -------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_harililamrut_singlefile.py <START_URL> [OUTPUT_FILE]")
        sys.exit(1)
    start_url = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else OUTPUT_FILE_DEFAULT
    run_all(start_url, output)

if __name__ == "__main__":
    main()
