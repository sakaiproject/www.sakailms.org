#!/usr/bin/env python3
"""Fail if a local link is missing or an external link does not return HTTP 200.

Root-absolute links such as /css/site.css break when the site is served from a
subdirectory (http://localhost:8888/www.sakailms.net/). Redirects are followed;
the final response must be 200. mailto and tel links are skipped. Facebook,
Twitter, and Instagram block automated requests, so those Sakai profiles are
skipped. GoFundMe is skipped because GitHub runners get 502s and timeouts.
A 429, 502, or timeout is retried before the check fails.
"""

import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
URL_ATTRS = {
    "a": ("href",),
    "link": ("href",),
    "img": ("src",),
    "iframe": ("src",),
    "script": ("src",),
    "source": ("src",),
    "video": ("src", "poster"),
    "audio": ("src",),
    "form": ("action",),
}
SKIP_SCHEMES = {"mailto", "tel", "javascript", "data"}
# These profiles work in a browser and reject GitHub Actions with 400, 403, or 429.
SKIP_PROFILES = {
    ("facebook.com", "/sakailms"),
    ("www.facebook.com", "/sakailms"),
    ("twitter.com", "/sakailms"),
    ("www.twitter.com", "/sakailms"),
    ("x.com", "/sakailms"),
    ("www.x.com", "/sakailms"),
    ("instagram.com", "/sakailms"),
    ("www.instagram.com", "/sakailms"),
}
SKIP_HOSTS = {"gofund.me", "www.gofund.me", "gofundme.com", "www.gofundme.com"}
TRANSIENT_STATUS = {429, 500, 502, 503, 504}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        element_id = attr_map.get("id")
        if element_id:
            self.ids.add(element_id)
        for name in URL_ATTRS.get(tag, ()):
            value = attr_map.get(name)
            if value is not None and value.strip():
                self.refs.append((self.getpos()[0], tag, name, value.strip()))


def classify(url):
    """Return 'skip', 'external', or 'local', and the URL to request."""
    candidate = "https:" + url if url.startswith("//") else url
    split = urlsplit(candidate)
    if split.scheme in SKIP_SCHEMES:
        return "skip", candidate
    if split.scheme in {"http", "https"}:
        host = split.netloc.lower()
        path = split.path.rstrip("/") or "/"
        if host in SKIP_HOSTS or (host, path.lower()) in SKIP_PROFILES:
            return "skip", candidate
        without_fragment = candidate.split("#", 1)[0]
        return "external", without_fragment
    return "local", url


def fetch_once(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read(512)
            return response.status, response.geturl(), None
    except urllib.error.HTTPError as error:
        return error.code, getattr(error, "url", url), None
    except Exception as error:
        return None, url, str(error)


def fetch_status(url):
    result = fetch_once(url)
    for attempt in range(2):
        status, _final, error = result
        if status == 200 or (status not in TRANSIENT_STATUS and not error):
            return result
        time.sleep(2 * (attempt + 1))
        result = fetch_once(url)
    return result


def locate(page, url):
    """Return the local file a page-relative URL should open, or an error string."""
    split = urlsplit(url)
    if split.scheme or split.netloc:
        return None, None
    raw_path = unquote(split.path)
    if raw_path.startswith("/"):
        return None, "root-absolute URL; use a relative path so subdirectory hosting works"
    base = page.parent
    if raw_path in ("", "."):
        target = base
    else:
        target = (base / raw_path).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError:
        return None, "path escapes the site root"
    if target.is_dir():
        index = target / "index.html"
        if not index.is_file():
            return None, "directory has no index.html"
        return index, split.fragment
    if not target.is_file():
        return None, "file does not exist"
    return target, split.fragment


def page_ids(path, cache):
    if path not in cache:
        parser = PageParser()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        cache[path] = parser
    return cache[path].ids


def check_html(path, id_cache):
    parser = PageParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    id_cache[path] = parser
    problems = []
    external = []
    for line, tag, attr, url in parser.refs:
        kind, target_url = classify(url)
        where = f"{path.relative_to(ROOT)}:{line}"
        if kind == "skip":
            continue
        if kind == "external":
            external.append((target_url, where))
            continue
        where = f"{where} <{tag} {attr}=\"{url}\">"
        target, detail = locate(path, url)
        if target is None:
            problems.append(f"{where} {detail}")
            continue
        fragment = detail
        if fragment and target.suffix.lower() in {".html", ".htm"}:
            if fragment not in page_ids(target, id_cache):
                problems.append(f"{where} missing id \"{fragment}\" in {target.relative_to(ROOT)}")
    return problems, external


def check_external(found):
    grouped = {}
    for url, where in found:
        grouped.setdefault(url, []).append(where)
    problems = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_status, url): url for url in grouped}
        for future in as_completed(futures):
            url = futures[future]
            status, final_url, error = future.result()
            if status == 200:
                continue
            places = ", ".join(grouped[url][:3])
            extra = f" (+{len(grouped[url]) - 3} more)" if len(grouped[url]) > 3 else ""
            if error:
                detail = error
            elif final_url and final_url != url:
                detail = f"HTTP {status} after redirect to {final_url}"
            else:
                detail = f"HTTP {status}"
            problems.append(f"{url} {detail} ({places}{extra})")
    return len(grouped), sorted(problems)


def main():
    pages = sorted(ROOT.rglob("*.html"))
    if not pages:
        print("no HTML files found", file=sys.stderr)
        return 1
    id_cache = {}
    problems = []
    external = []
    for page in pages:
        page_problems, page_external = check_html(page, id_cache)
        problems.extend(page_problems)
        external.extend(page_external)
    external_count, external_problems = check_external(external)
    if problems:
        print(f"{len(problems)} broken local link(s):", file=sys.stderr)
        for problem in problems:
            print(problem, file=sys.stderr)
    if external_problems:
        print(f"{len(external_problems)} external link(s) did not return 200:", file=sys.stderr)
        for problem in external_problems:
            print(problem, file=sys.stderr)
    if problems or external_problems:
        return 1
    print(
        f"checked {len(pages)} pages; "
        f"local targets exist and {external_count} external links returned 200"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
