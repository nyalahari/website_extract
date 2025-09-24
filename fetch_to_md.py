#!/usr/bin/env python3
"""
extract_harililamrut.py

Fetch the given Harililamrut page and the next page (via <a class="nav_right">),
include footnotes, remove the page <title> and the header <h1> with the header image,
convert to Markdown, and save one .md file per page.

Usage:
  python extract_harililamrut.py "https://anirdesh.com/harililamrut/index.php?kalash=1&vishram=1"

Notes:
  - This script intentionally does NOT check robots.txt (per your request).
  - Only follows one "next" link (a with class "nav_right") once.
  - Modify REQUEST_DELAY if you want longer pauses between requests.
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
REQUEST_DELAY = 0.8   # seconds between requests (change if you want)
OUTPUT_FILENAME_TEMPLATE = "harililamrut_kalash{kalash}_vishram{vishram}.md"
# -------------------------

def fetch_html(url):
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.content

def clean_and_extract(soup, base_url):
    """
    Clean the soup for conversion to Markdown:
      - remove <title> tag
      - remove specific <h1> that contains the site header image (harililamrut-header.jpg)
      - remove scripts/styles and some non-content blocks
      - locate main/article content heuristically
      - extract footnotes from <div id="footnotes" ...> and return separately
    Returns: (content_tag_or_html_string, footnotes_html, page_title_text_or_none)
    """
    # Remove <title> tag if present
    if soup.title:
        soup.title.decompose()

    # Remove any <h1> that contains the header image (harililamrut-header.jpg)
    for h1 in soup.find_all("h1"):
        img = h1.find("img")
        if img and "harililamrut-header" in (img.get("src") or ""):
            h1.decompose()

    # Remove scripts, styles, noscript, iframes, forms, svg, etc.
    for tagname in ("script", "style", "noscript", "iframe", "form", "input", "button", "svg"):
        for t in soup.find_all(tagname):
            t.decompose()

    # Remove navigation/footer areas that tend not to be article content
    for t in soup.find_all(["nav", "header", "footer", "aside"]):
        # but be careful not to remove nav_right if we need it to find the link;
        # we will find nav_right before this function if necessary.
        t.decompose()

    # Heuristic: prefer <main>, <article>, or large content-like divs
    content_candidate = None
    if soup.find("main"):
        content_candidate = soup.find("main")
    elif soup.find("article"):
        content_candidate = soup.find("article")
    else:
        # try common id/class names
        candidates = []
        for attr in ("id", "class"):
            for name in ("content", "main", "page", "article", "container", "wrapper", "post", "entry"):
                found = soup.find(attrs={attr: lambda v: v and name in v})
                if found and found.get_text(strip=True):
                    candidates.append(found)
        if candidates:
            content_candidate = max(candidates, key=lambda t: len(t.get_text(" ", strip=True)))
        else:
            # fallback to body
            content_candidate = soup.body or soup

    # Remove internal sidebars inside content_candidate if any
    for side in content_candidate.find_all(attrs={"class": lambda v: v and ("sidebar" in v or "nav" in v)}):
        side.decompose()

    # Extract footnotes HTML if present (div id="footnotes")
    footnotes_div = soup.find("div", id="footnotes")
    footnotes_html = ""
    if footnotes_div:
        # Copy footnotes html and then remove from document to avoid duplication
        footnotes_html = str(footnotes_div)
        footnotes_div.decompose()

    # Convert selected content_candidate to string
    content_html = str(content_candidate)

    # Attempt to get a page title from content (e.g., if there is a visible heading)
    page_title_text = None
    possible_h1 = content_candidate.find("h1")
    if possible_h1 and possible_h1.get_text(strip=True):
        page_title_text = possible_h1.get_text(strip=True)

    return content_html, footnotes_html, page_title_text

def html_to_markdown(content_html, footnotes_html, filename_title):
    """
    Convert content_html + footnotes_html to Markdown text.
    Exclude original <title> content (we removed it earlier).
    Prepend a simple header that uses filename_title to identify the page (not the <title> tag).
    """
    # Convert main content
    md_main = md(content_html, heading_style="ATX")

    # Convert footnotes if present
    md_footnotes = ""
    if footnotes_html and footnotes_html.strip():
        md_footnotes = md(footnotes_html, heading_style="ATX")
        # Normalize heading for footnotes
        if not md_footnotes.lower().startswith("##") and not md_footnotes.lower().startswith("#"):
            md_footnotes = "## Footnotes\n\n" + md_footnotes

    # Cleanup extra blank lines
    full_md = md_main.strip()
    if md_footnotes:
        full_md = full_md + "\n\n" + md_footnotes.strip()

    # Add a minimal top header (this is not the page's <title> tag)
    header = f"# {filename_title}\n\n"
    result = header + full_md
    # Collapse multiple blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result

def make_filename_from_url(url):
    """
    Attempt to extract kalash and vishram query params for friendly filename.
    Fallback to a safe slug if not present.
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    kalash = qs.get("kalash", [None])[0]
    vishram = qs.get("vishram", [None])[0]
    if kalash and vishram:
        return OUTPUT_FILENAME_TEMPLATE.format(kalash=kalash, vishram=vishram)
    # fallback: use path and last part
    path = parsed.path.strip("/").replace("/", "_") or "page"
    # also include query if any (safe chars only)
    qsafe = re.sub(r'[^0-9A-Za-z_-]', '_', parsed.query)[:50]
    if qsafe:
        return f"{path}_{qsafe}.md"
    return f"{path}.md"

def friendly_title_from_url(url):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    kalash = qs.get("kalash", [None])[0]
    vishram = qs.get("vishram", [None])[0]
    if kalash and vishram:
        return f"Harililamrut — Kalash {kalash} / Vishram {vishram}"
    # fallback
    return parsed.path.strip("/") or url

def process_page(url, base_url=None, write_file=True):
    print(f"Fetching: {url}")
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    # BEFORE removing nav, capture the next link (nav_right)
    next_anchor = soup.find("a", class_=lambda v: v and "nav_right" in v)
    next_url = None
    if next_anchor and next_anchor.get("href"):
        # resolve relative hrefs
        base = base_url or url
        next_url = urljoin(base, next_anchor.get("href"))

    # Clean and extract content + footnotes
    content_html, footnotes_html, page_title_text = clean_and_extract(soup, url)
    filename = make_filename_from_url(url)
    title_for_file = friendly_title_from_url(url)

    md_text = html_to_markdown(content_html, footnotes_html, title_for_file)

    if write_file:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(md_text)
        print(f"Wrote Markdown: {filename}")

    return {
        "url": url,
        "filename": filename,
        "next_url": next_url,
        "md": md_text
    }

def main(entry_url):
    # Process first page
    result1 = process_page(entry_url)
    # Short delay before next fetch
    if result1["next_url"]:
        print("Found next page link:", result1["next_url"])
        time.sleep(REQUEST_DELAY)
        result2 = process_page(result1["next_url"])
    else:
        print("No next page link found (class nav_right).")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_harililamrut.py <URL>")
        sys.exit(1)
    entry = sys.argv[1]
    main(entry)
