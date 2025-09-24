#!/usr/bin/env python3
"""
fetch_to_md_no_robots.py

Fetch a URL and save cleaned Markdown — DOES NOT CHECK robots.txt.

Usage:
    python fetch_to_md_no_robots.py "<URL>" "<OUTPUT.md>"

Dependencies:
    pip install requests beautifulsoup4 lxml markdownify

WARNING: This script intentionally does NOT consult robots.txt.
Only use when you have permission or you are certain your use is allowed.
"""

import sys
import re
import time
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# -------------------------
# Config - edit if needed
# -------------------------
USER_AGENT = (
    "Mozilla/5.0 (compatible; FetchToMD/1.0; +https://github.com/yourname) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
REQUEST_TIMEOUT = 25  # seconds
DEFAULT_DELAY = 0.5   # polite short delay between requests (you may increase)

# -------------------------
# Helpers
# -------------------------
def guess_main_content(soup):
    """Heuristic: prefer <main> or <article>, else pick largest candidate."""
    main = soup.find("main")
    if main and main.get_text(strip=True):
        return main
    article = soup.find("article")
    if article and article.get_text(strip=True):
        return article

    candidates = []
    for attr in ("id", "class"):
        for name in ("content", "main", "page", "article", "container", "wrapper", "post", "entry"):
            sel = soup.find(attrs={attr: lambda v: v and name in v})
            if sel and sel.get_text(strip=True):
                candidates.append(sel)

    if candidates:
        return max(candidates, key=lambda t: len(t.get_text(" ", strip=True)))

    return soup.body or soup

def clean_html(soup):
    """Remove unwanted tags and sidebars from the soup."""
    # Remove scripts/styles and other non-content elements
    for tag in soup(["script", "style", "noscript", "iframe", "form", "input", "button", "svg"]):
        tag.decompose()
    # Remove header/footer/nav/aside which are often non-essential
    for t in soup.find_all(["nav", "header", "footer", "aside"]):
        t.decompose()
    return soup

# -------------------------
# Main fetch & convert
# -------------------------
def fetch_and_convert(url, outpath, delay=DEFAULT_DELAY):
    # optional short delay to be slightly polite
    if delay and delay > 0:
        time.sleep(delay)

    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        print("Request error:", e)
        sys.exit(2)

    if resp.status_code != 200:
        print(f"Server returned status {resp.status_code} for URL: {url}")
        sys.exit(3)

    soup = BeautifulSoup(resp.content, "lxml")
    soup = clean_html(soup)
    main_tag = guess_main_content(soup)
    # remove common sidebar-like children inside main_tag
    for side in main_tag.find_all(attrs={"class": lambda v: v and ("sidebar" in v or "nav" in v)}):
        side.decompose()

    html_content = str(main_tag)
    # Convert HTML to Markdown
    markdown_text = md(html_content, heading_style="ATX")

    # tidy up extra blank lines and trailing whitespace
    markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text)
    markdown_text = "\n".join([line.rstrip() for line in markdown_text.splitlines()])

    # prepend a simple title header
    title = soup.title.string.strip() if soup.title and soup.title.string else url
    front = f"# {title}\n\n"
    full_text = front + markdown_text

    # write file
    with open(outpath, "w", encoding="utf-8") as f:
        f.write(full_text)

    print("Saved:", outpath)

# -------------------------
# CLI
# -------------------------
def main():
    if len(sys.argv) < 3:
        print("Usage: python fetch_to_md_no_robots.py <URL> <OUTPUT_MD>")
        sys.exit(1)
    url = sys.argv[1]
    out = sys.argv[2]
    fetch_and_convert(url, out)

if __name__ == "__main__":
    main()


