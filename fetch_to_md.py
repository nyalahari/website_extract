#!/usr/bin/env python3
"""
extract_harililamrut_resilient.py

Robust scraper for Anirdesh Harililamrut that:
 - does NOT check robots.txt (per user request)
 - follows <a class="nav_right"> repeatedly
 - detects DB/server error pages (e.g., max_user_connections, SQLSTATE, PHP fatal)
 - uses exponential backoff & retries on those errors
 - appends each successful page to a SINGLE output markdown file
 - saves progress after each page

Usage:
  python extract_harililamrut_resilient.py "<START_URL>" [output.md]

Config options are near the top of the file.
"""

import sys
import time
import re
import json
from urllib.parse import urljoin, urlparse, parse_qs
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from datetime import datetime

# -------------------------
# Configuration
# -------------------------
USER_AGENT = (
    "Mozilla/5.0 (compatible; HarililamrutScraper/1.0; +https://github.com/yourname) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
REQUEST_TIMEOUT = 25
REQUEST_DELAY = 0.8         # base polite delay between *successful* requests
MAX_PAGES = 1000            # safety cap to avoid infinite loops
OUTPUT_FILE_DEFAULT = "all_harililamrut.md"
PROGRESS_FILE = ".harililamrut_progress.json"  # saves visited list + last url (optional)
# Fetch/backoff policy
MAX_FETCH_ATTEMPTS = 6      # how many times to retry each page on server/db error
BACKOFF_INITIAL = 6         # seconds (initial backoff)
BACKOFF_MULTIPLIER = 2      # exponential multiplier
# Patterns that indicate the site is returning a DB/server error in the HTML body
ERROR_PATTERNS = [
    r"max_user_connections",                       # MySQL max connections message
    r"SQLSTATE\[HY000\] \[1203\]",                 # explicit SQLSTATE message
    r"already has more than 'max_user_connections'",
    r"Call to a member function query\(\) on null", # PHP fatal from your earlier message
    r"Fatal error",                                # generic PHP fatal
    r"Maximum execution time",                     # PHP timeout message
    r"Service temporarily unavailable",            # friendly 503 messages
    r"database is unavailable",
]
# -------------------------

def save_progress(visited, next_url, output_file):
    """Save minimal progress so a run can be resumed/inspected."""
    data = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "visited": list(visited),
        "next_url": next_url,
        "output_file": output_file
    }
    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print("Warning: couldn't save progress file:", e)

def load_progress():
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def looks_like_error_page(text):
    """Return True if HTML text contains known server/DB error patterns."""
    if not text:
        return True
    lower = text.lower()
    for pat in ERROR_PATTERNS:
        if re.search(pat.lower(), lower):
            return True
    # Also treat empty body or very short body as suspicious
    if len(text.strip()) < 100:
        return True
    return False

def fetch_with_backoff(url, max_attempts=MAX_FETCH_ATTEMPTS,
                       backoff_initial=BACKOFF_INITIAL, timeout=REQUEST_TIMEOUT):
    """
    Fetch URL with exponential backoff when server returns 5xx, or body matches error patterns.
    Returns response.text (HTML) when successful, else raises Exception after retries.
    """
    attempt = 0
    backoff = backoff_initial
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}

    while attempt < max_attempts:
        attempt += 1
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            print(f"[Attempt {attempt}] Network error: {e}. Backing off {backoff}s.")
            time.sleep(backoff)
            backoff *= BACKOFF_MULTIPLIER
            continue

        status = resp.status_code
        body = resp.text or ""

        # If server status is 5xx or 429, consider retrying
        if status >= 500 or status == 429:
            print(f"[Attempt {attempt}] Server returned status {status}. Backing off {backoff}s.")
            time.sleep(backoff)
            backoff *= BACKOFF_MULTIPLIER
            continue

        # If 200 but page content shows DB/PHP error, backoff and retry
        if status == 200 and looks_like_error_page(body):
            print(f"[Attempt {attempt}] Page content indicates server/DB error. Backing off {backoff}s.")
            # Give a longer backoff in presence of DB-specific text
            time.sleep(backoff)
            backoff *= BACKOFF_MULTIPLIER
            continue

        # If 200 and content looks OK, return it
        if status == 200:
            return body

        # For other statuses (3xx/4xx non-429), return whatever the server gave but notify
        print(f"[Attempt {attempt}] Non-200 status {status}. Returning content (not retrying).")
        return body

    # exhausted attempts
    raise RuntimeError(f"Failed to fetch {url} after {max_attempts} attempts; last status {status if 'status' in locals() else 'N/A'}")

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

    # Extract footnotes div if present, then remove it
    footnotes_div = soup.find("div", id="footnotes")
    footnotes_html = ""
    if footnotes_div:
        footnotes_html = str(footnotes_div)
        footnotes_div.decompose()

    # Remove nav/header/footer/aside to reduce noise
    for t in soup.find_all(["nav", "header", "footer", "aside"]):
        t.decompose()

    # Determine main content
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

    visible_title = None
    h1 = content_candidate.find("h1")
    if h1 and h1.get_text(strip=True):
        visible_title = h1.get_text(strip=True)

    return str(content_candidate), footnotes_html, visible_title

def html_to_markdown_for_page(content_html, footnotes_html, page_label):
    """Convert HTML to markdown and return (page_md, md_footnotes)"""
    md_main = md(content_html, heading_style="ATX").strip()
    md_footnotes = ""
    if footnotes_html and footnotes_html.strip():
        md_footnotes = md(footnotes_html, heading_style="ATX").strip()
    page_header = f"\n\n---\n\n## {page_label}\n\n"
    return page_header + md_main, md_footnotes

def find_next_link(soup, base_url):
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

def safe_append_to_file(output_file, text):
    """Append text to file (UTF-8)."""
    with open(output_file, "a", encoding="utf-8") as f:
        f.write(text)

def run_resilient(start_url, output_file=OUTPUT_FILE_DEFAULT,
                  delay=REQUEST_DELAY, max_pages=MAX_PAGES):
    # If output file exists, we will append; optionally you can choose to overwrite by deleting first.
    visited = set()
    # If progress file exists, offer to resume (simple behavior: load visited list)
    progress = load_progress()
    if progress and "visited" in progress:
        # we will not auto-resume to avoid surprises, but we can pre-populate visited to avoid re-fetching
        try:
            resume = False
            # If you want auto-resume behavior, uncomment following line:
            # resume = True
            if resume:
                visited.update(progress.get("visited", []))
                print("Resuming run with visited preloaded (from progress file).")
        except Exception:
            pass

    current = start_url
    page_count = 0
    collected_footnotes = []  # tuples (page_label, md_footnotes)

    # Write header if output file is new
    try:
        open(output_file, "x", encoding="utf-8").close()
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(f"# Harililamrut — Combined Extract (generated {datetime.utcnow().isoformat()}Z)\n\n")
    except FileExistsError:
        # file exists — we append
        print(f"Appending to existing file: {output_file}")

    while current and page_count < max_pages:
        if current in visited:
            print("Already visited, stopping to avoid loop:", current)
            break

        print(f"\n---\nFetching page {page_count+1}: {current}")
        try:
            html = fetch_with_backoff(current)
        except Exception as e:
            print("Failed to fetch page after retries:", e)
            print("Stopping run to avoid saving broken content.")
            break

        # Parse the HTML and capture next link before cleaning
        soup = BeautifulSoup(html, "lxml")
        next_link = find_next_link(soup, current)

        # Clean/extract and convert
        content_html, footnotes_html, visible_title = clean_and_extract_parts(soup)
        page_label = make_page_label(current, visible_title)
        page_md, md_footnotes = html_to_markdown_for_page(content_html, footnotes_html, page_label)

        # Append page markdown to output file
        safe_append_to_file(output_file, page_md + "\n\n")
        if md_footnotes:
            collected_footnotes.append((page_label, md_footnotes))

        visited.add(current)
        page_count += 1
        save_progress(visited, next_link, output_file)

        # Polite delay between successful fetches
        if delay:
            time.sleep(delay)

        # Advance to next
        if next_link:
            parsed_next = urlparse(next_link)
            next_link = parsed_next._replace(fragment="").geturl()
            if next_link in visited:
                print("Next link already visited; finishing.")
                break
            current = next_link
        else:
            print("No next link found; finishing crawl.")
            current = None

    # After loop, append collected footnotes grouped by page
    if collected_footnotes:
        footer = "\n\n---\n\n## Footnotes (combined)\n\n"
        for page_label, md_footnotes in collected_footnotes:
            footer += f"### {page_label}\n\n{md_footnotes}\n\n"
        footer = re.sub(r"\n{3,}", "\n\n", footer)
        safe_append_to_file(output_file, footer)

    print(f"\nFinished. Pages fetched: {page_count}. Output: {output_file}")
    # final progress save
    save_progress(visited, None, output_file)

# -------------------------
# CLI
# -------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_harililamrut_resilient.py <START_URL> [OUTPUT_FILE]")
        sys.exit(1)
    start = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else OUTPUT_FILE_DEFAULT
    run_resilient(start, out)

if __name__ == "__main__":
    main()
