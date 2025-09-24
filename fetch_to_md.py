#!/usr/bin/env python3
"""
fetch_to_md.py
Fetch a web page and save cleaned Markdown.

Usage (from GitHub Actions or any Python runtime):
  python fetch_to_md.py "https://..." "output.md"

This script uses:
  requests, beautifulsoup4, lxml, markdownify
"""

import sys, re
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/120.0 Safari/537.36")
REQUEST_TIMEOUT = 20

def allowed_by_robots(url):
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        r = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    except Exception:
        return True
    if r.status_code != 200:
        return True
    txt = r.text.lower()
    if "user-agent: *" in txt and "disallow: /" in txt:
        return False
    return True

def guess_main_content(soup):
    main = soup.find("main")
    if main and main.get_text(strip=True):
        return main
    article = soup.find("article")
    if article and article.get_text(strip=True):
        return article
    candidates = []
    for attr in ("id", "class"):
        for name in ("content", "main", "page", "article", "container", "wrapper", "post"):
            sel = soup.find(attrs={attr: lambda v: v and name in v})
            if sel and sel.get_text(strip=True):
                candidates.append(sel)
    if candidates:
        return max(candidates, key=lambda t: len(t.get_text(" ", strip=True)))
    return soup.body or soup

def fetch_and_convert(url, outpath):
    if not allowed_by_robots(url):
        raise SystemExit("Blocked by robots.txt — not fetching (polite).")
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    if r.status_code != 200:
        raise SystemExit(f"Server returned {r.status_code}")
    soup = BeautifulSoup(r.content, "lxml")
    for tag in soup(["script","style","noscript","iframe","form","input","button","svg"]):
        tag.decompose()
    for t in soup.find_all(["nav","header","footer","aside"]):
        t.decompose()
    main = guess_main_content(soup)
    for side in main.find_all(attrs={"class": lambda v: v and ("sidebar" in v or "nav" in v)}):
        side.decompose()
    html_content = str(main)
    markdown_text = md(html_content, heading_style="ATX")
    markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text)
    markdown_text = "\n".join([line.rstrip() for line in markdown_text.splitlines()])
    title = soup.title.string.strip() if soup.title and soup.title.string else url
    with open(outpath, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(markdown_text)
    print("Saved:", outpath)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python fetch_to_md.py <URL> <OUTPUT_MD>")
        sys.exit(1)
    fetch_and_convert(sys.argv[1], sys.argv[2])
