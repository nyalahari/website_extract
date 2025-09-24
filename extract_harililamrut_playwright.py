#!/usr/bin/env python3
"""
extract_harililamrut_playwright.py

Render the page with Playwright (runs JS), then extract cleaned markdown with
the same footnote & nav-clean rules we built earlier.

Usage:
  # install deps first (see instructions below)
  python extract_harililamrut_playwright.py "https://anirdesh.com/harililamrut/index.php?kalash=1&vishram=1" out.md

Important:
 - This script intentionally does NOT check robots.txt (per your request).
 - Rendering JS uses a headless browser and is heavier than plain requests.
 - If you run this in GitHub Actions, it will take more minutes than a basic request job.
"""

import sys, re, time
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, NavigableString
from markdownify import markdownify as md

# Playwright import
from playwright.sync_api import sync_playwright

# ---------- Config ----------
USER_AGENT = ("Mozilla/5.0 (compatible; HarililamrutPlaywright/1.0; +https://github.com/you) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
REQUEST_DELAY = 0.6
# ---------------------------

# --- Reuse the cleaning/footnote logic from the v2 script (slightly trimmed) ---
NAV_KEYWORDS = ["વિશ્રામ", "કળશ", "Kalash", "Vishram"]
MAX_LINKS_IN_BLOCK = 12
NAV_LINK_PATTERNS = [
    "/vachanamrut/", "/vato/", "/kirtan/", "/kavya/",
    "/aksharamrutam/", "/chintamani/"
]

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
    removed_any = False
    candidates = soup.find_all(['div', 'nav', 'aside', 'ul', 'section'], recursive=True)
    for el in candidates:
        anchors = el.find_all('a')
        if len(anchors) >= MAX_LINKS_IN_BLOCK:
            avg_len = sum(len(a.get_text(strip=True)) for a in anchors) / max(1, len(anchors))
            short_links = sum(1 for a in anchors if len(a.get_text(strip=True)) < 40)
            if short_links >= max(10, int(0.8 * len(anchors))) or avg_len < 40:
                internal = sum(1 for a in anchors if (a.get('href') or "").startswith("index.php") or (a.get('href') or "").startswith("/"))
                if internal >= max(5, int(0.5 * len(anchors))):
                    el.decompose()
                    removed_any = True
                    continue
        txt = el.get_text(" ", strip=True)
        keyword_hits = sum(txt.count(k) for k in NAV_KEYWORDS)
        if keyword_hits >= 6:
            el.decompose()
            removed_any = True
    return removed_any

def extract_footnotes(soup):
    footnotes_div = soup.find("div", id="footnotes") or soup.find("div", class_=lambda v: v and "footnotes" in v)
    if not footnotes_div:
        return []
    results = []
    ol = footnotes_div.find("ol")
    if ol:
        items = ol.find_all("li", recursive=False)
        for i, li in enumerate(items, start=1):
            for back in li.find_all('a'):
                href = (back.get('href') or "")
                cls = " ".join(back.get('class') or [])
                if href.startswith("#") or 'back' in cls.lower() or 'fnref' in cls.lower():
                    back.decompose()
            for sup in li.find_all('sup'):
                if sup.find('a'):
                    sup.decompose()
            inner_html = "".join(str(c) for c in li.contents).strip()
            converted = md(inner_html, heading_style="ATX").strip() or li.get_text(" ", strip=True)
            results.append({"id": li.get('id') or f"fn-{i}", "num": i, "html": inner_html, "md": converted})
    else:
        children = [c for c in footnotes_div.find_all(recursive=False) if getattr(c, 'name', None) in ('li','div','p')]
        if children:
            for i, el in enumerate(children, start=1):
                for back in el.find_all('a'):
                    href = (back.get('href') or "")
                    cls = " ".join(back.get('class') or [])
                    if href.startswith("#") or 'back' in cls.lower() or 'fnref' in cls.lower():
                        back.decompose()
                inner_html = "".join(str(c) for c in el.contents).strip()
                converted = md(inner_html, heading_style="ATX").strip() or el.get_text(" ", strip=True)
                results.append({"id": el.get('id') or f"fn-{i}", "num": i, "html": inner_html, "md": converted})
        else:
            inner_html = "".join(str(c) for c in footnotes_div.contents).strip()
            converted = md(inner_html, heading_style="ATX").strip() or footnotes_div.get_text(" ", strip=True)
            results.append({"id": footnotes_div.get('id') or "fn-1", "num": 1, "html": inner_html, "md": converted})
    footnotes_div.decompose()
    return results

def replace_inline_footnote_refs(soup, footnotes):
    id_to_num = {entry['id']: entry['num'] for entry in footnotes}
    known_nums = set(entry['num'] for entry in footnotes)
    for a in list(soup.find_all('a')):
        href = (a.get('href') or "")
        text = a.get_text("", strip=True)
        cls = " ".join(a.get('class') or [])
        replaced = False
        if href.startswith("#"):
            target = href[1:]
            if target in id_to_num:
                num = id_to_num[target]
                a.replace_with(NavigableString(f"[{num}]"))
                replaced = True
            else:
                m = re.search(r'(\d+)', target)
                if m:
                    n = int(m.group(1))
                    if n in known_nums:
                        a.replace_with(NavigableString(f"[{n}]"))
                        replaced = True
        if not replaced and (re.search(r'fnref|footnote', cls, re.I) or text.isdigit()):
            if len(text) <= 6:
                a.replace_with(NavigableString(f"[{text}]"))
                replaced = True
    for s in list(soup.find_all('sup')):
        txt = s.get_text("", strip=True)
        if txt.isdigit() and len(txt) <= 4:
            s.replace_with(NavigableString(f"[{txt}]"))

def find_main_content(soup):
    if soup.find("main"):
        return soup.find("main")
    if soup.find("article"):
        return soup.find("article")
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
    for t in main.find_all(["nav","header","footer","aside"]):
        t.decompose()
    html_content = str(main)
    md_main = md(html_content, heading_style="ATX").strip()
    md_main = re.sub(r'^(#{1,6}\s*Kalash\s*\d+\s*/\s*Vishram\s*\d+\s*\n)+', '', md_main, flags=re.I)
    foot_md = ""
    if footnotes:
        parts = []
        for e in footnotes:
            txt = e.get('md') or e.get('html') or ''
            txt = txt.strip()
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

# ---- Rendering function ----
def render_page_via_playwright(url, wait_for_selector=None, timeout=30000):
    """
    Render the page using Playwright and return the final HTML.
    wait_for_selector: if given, wait until that selector appears (helps with SPA pages).
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.goto(url, timeout=timeout)
        # prefer waiting for either footnotes container or main content to appear, else networkidle
        try:
            if wait_for_selector:
                page.wait_for_selector(wait_for_selector, timeout=timeout)
            else:
                # try some reasonable selectors
                for sel in ("div#footnotes", "main", "article", "div.content", "div#content"):
                    try:
                        page.wait_for_selector(sel, timeout=3000)
                        break
                    except:
                        pass
                # ensure network idle as final fallback
                page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            # continue anyway
            pass
        content = page.content()
        page.close()
        context.close()
        browser.close()
        return content

def process_with_render(url, output_file):
    print("Rendering:", url)
    html = render_page_via_playwright(url)
    soup = BeautifulSoup(html, "lxml")

    removed_top = remove_top_kalash_heading(soup)
    if removed_top:
        print("Removed top Kalash/Vishram heading")
    if remove_nav_table(soup):
        print("Removed nav table")
    if remove_large_nav_blocks(soup):
        print("Removed large nav blocks")

    footnotes = extract_footnotes(soup)
    if footnotes:
        print(f"Extracted {len(footnotes)} footnotes")

    replace_inline_footnote_refs(soup, footnotes)
    convert_and_write(soup, footnotes, output_file)

# ---- CLI ----
def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_harililamrut_playwright.py <URL> [output.md]")
        sys.exit(1)
    url = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "page_harililamrut_rendered.md"
    process_with_render(url, out)

if __name__ == "__main__":
    main()
