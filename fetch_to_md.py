#!/usr/bin/env python3
"""
fetch_to_md.py - fetch a page and save cleaned Markdown.

Usage:
  python fetch_to_md.py "<URL>" "<OUTPUT.md>"
  python fetch_to_md.py "<URL>" "<OUTPUT.md>" --ignore-robots

Environment:
  IGNORE_ROBOTS=1   # (optional) also acts as override if set in env

Important:
  - Only use --ignore-robots if you have permission to scrape the site.
"""

import sys, re, os, time, argparse
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
REQUEST_TIMEOUT = 20
DEFAULT_DELAY = 1.0  # seconds between requests (polite)

def allowed_by_robots(url):
    """Simple robots.txt check; returns True if allowed or if robots can't be read."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        r = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return True
        txt = r.text.lower()
        if "user-agent: *" in txt and "disallow: /" in txt:
            return False
    except Exception:
        # If robots can't be fetched, default to allowing (but you can adjust policy)
        return True
    return True

def guess_main_content(soup):
    main = soup.find("main")
    if main and main.get_text(strip=True):
        return main
    article = soup.find("article")
    if article and article.get_text(strip=True):
        return article
    candidates = []
    for attr in ("id","class"):
        for name in ("content","main","page","article","container","wrapper","post"):
            sel = soup.find(attrs={attr: lambda v: v and name in v})
            if sel and sel.get_text(strip=True):
                candidates.append(sel)
    if candidates:
        return max(candidates, key=lambda t: len(t.get_text(" ", strip=True)))
    return soup.body or soup

def fetch_and_convert(url, outpath, ignore_robots=False, delay=DEFAULT_DELAY):
    if not ignore_robots and not allowed_by_robots(url):
        raise SystemExit("Blocked by robots.txt — not fetching (polite).")
    # polite small delay
    time.sleep(delay)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise SystemExit(f"Request failed: {e}")
    if r.status_code != 200:
        raise SystemExit(f"Server returned status {r.status_code} for URL: {url}")

    soup = BeautifulSoup(r.content, "lxml")
    for tag in soup(["script","style","noscript","iframe","form","input","button","svg"]):
        tag.decompose()
    for t in soup.find_all(["nav","header","footer","aside"]):
        t.decompose()
    main_tag = guess_main_content(soup)
    for side in main_tag.find_all(attrs={"class": lambda v: v and ("sidebar" in v or "nav" in v)}):
        side.decompose()
    html_content = str(main_tag)
    markdown_text = md(html_content, heading_style="ATX")
    markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text)
    markdown_text = "\n".join([line.rstrip() for line in markdown_text.splitlines()])
    title = soup.title.string.strip() if soup.title and soup.title.string else url
    with open(outpath, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(markdown_text)
    print("Saved:", outpath)

def parse_args():
    p = argparse.ArgumentParser(description="Fetch a URL and save as Markdown")
    p.add_argument("url", help="URL to fetch")
    p.add_argument("out", help="Output markdown filename")
    p.add_argument("--ignore-robots", action="store_true",
                   help="Ignore robots.txt (only use if you have permission!)")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Seconds to wait before request")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    # env var override as well
    env_ignore = os.environ.get("IGNORE_ROBOTS", "").strip() in ("1","true","True")
    ignore_flag = args.ignore_robots or env_ignore
    try:
        fetch_and_convert(args.url, args.out, ignore_robots=ignore_flag, delay=args.delay)
    except SystemExit as e:
        print("Error:", e)
        sys.exit(1)
    except Exception as e:
        print("Unexpected error:", e)
        sys.exit(2)
