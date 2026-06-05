"""
Web search and fetch tools for cc_mine.

- web_search: parallel search across multiple engines (Baidu + SerpAPI)
- web_fetch: fetch a URL and extract readable text
"""

import html as _html_lib
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.parse import urlparse

_httpx = None  # lazy-loaded when needed

# ── Configuration ──
MAX_FETCH_SIZE = 100 * 1024
MAX_FETCH_TEXT = 8000
FETCH_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; cc_mine/1.0; +https://github.com/cc_mine)"
SEARCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Search engines
BAIDU_URL = "https://www.baidu.com/s"
SERPAPI_URL = "https://serpapi.com/search"
MAX_RESULTS_PER_ENGINE = 8
MAX_TOTAL_RESULTS = 15


def _get_httpx():
    global _httpx
    if _httpx is None:
        try:
            import httpx
            _httpx = httpx
        except ImportError:
            return None
    return _httpx


# ── HTML to Text ──
class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'head', 'title'):
            self.skip = True

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'head', 'title'):
            self.skip = False
        if tag in ('p', 'br', 'div', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.text.append('\n')

    def handle_data(self, data):
        if not self.skip:
            s = data.strip()
            if s:
                self.text.append(s)

    def get_text(self) -> str:
        raw = ' '.join(self.text)
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        raw = re.sub(r' {2,}', ' ', raw)
        return raw.strip()


def _extract_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    text = parser.get_text()
    if len(text) > MAX_FETCH_TEXT:
        text = text[:MAX_FETCH_TEXT] + f"\n... (truncated, {len(text) - MAX_FETCH_TEXT} more chars)"
    return text


# ── Search Engine Implementations ──

def _search_baidu(query: str, httpx) -> list[dict]:
    """Search Baidu. Returns list of {title, url, snippet, engine}."""
    try:
        resp = httpx.get(
            BAIDU_URL,
            params={"wd": query},
            headers={
                "User-Agent": SEARCH_UA,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return []

    results = []
    title_pattern = re.compile(
        r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE
    )
    snippet_pattern = re.compile(
        r'<(?:span|div)[^>]*class=["\'](?:c-abstract|content-right_[^"\']*)["\'][^>]*>(.*?)</(?:span|div)>',
        re.DOTALL | re.IGNORECASE
    )

    title_matches = title_pattern.findall(html)
    snippet_matches = snippet_pattern.findall(html)

    for i, (url, title_html) in enumerate(title_matches[:MAX_RESULTS_PER_ENGINE]):
        title = _html_lib.unescape(re.sub(r'<[^>]+>', '', title_html).strip())
        if not title or not url:
            continue
        snippet = ""
        if i < len(snippet_matches):
            snippet = _html_lib.unescape(
                re.sub(r'<[^>]+>', '', snippet_matches[i]).strip()
            )
        results.append({"title": title, "url": url, "snippet": snippet, "engine": "baidu"})

    return results


def _search_serpapi(query: str, httpx) -> list[dict]:
    """Search via SerpAPI (Google results). Requires SERPAPI_KEY env var."""
    api_key = os.getenv("SERPAPI_KEY", "").strip()
    if not api_key:
        return []

    try:
        resp = httpx.get(
            SERPAPI_URL,
            params={
                "q": query,
                "api_key": api_key,
                "engine": "google",
                "num": MAX_RESULTS_PER_ENGINE,
            },
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    results = []
    for item in data.get("organic_results", [])[:MAX_RESULTS_PER_ENGINE]:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": item.get("snippet", ""),
            "engine": "serpapi",
        })

    # Also include answer box if present
    answer = data.get("answer_box", {})
    if answer:
        answer_title = answer.get("title") or answer.get("result") or ""
        answer_url = answer.get("link", "")
        answer_snippet = answer.get("snippet") or answer.get("answer") or ""
        if answer_title or answer_snippet:
            results.insert(0, {
                "title": f"[Answer] {answer_title}" if answer_title else "[Answer]",
                "url": answer_url,
                "snippet": str(answer_snippet)[:300],
                "engine": "serpapi",
            })

    return results


# ── Engine Registry ──
_SEARCH_ENGINES = [
    _search_baidu,  # always available
    _search_serpapi,  # requires SERPAPI_KEY
]


# ── Public API ──

def run_web_search(query: str,
                   allowed_domains: list[str] | None = None,
                   blocked_domains: list[str] | None = None) -> str:
    """Search the web using multiple engines in parallel.

    Runs Baidu + SerpAPI (if configured) concurrently, merges and
    deduplicates results. Set SERPAPI_KEY env var to enable Google results.
    """
    httpx = _get_httpx()
    if not httpx:
        return "Error: 'httpx' package not installed. Run: pip install httpx"

    # ── Parallel search across all engines ──
    all_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(_SEARCH_ENGINES)) as pool:
        futures = {pool.submit(engine, query, httpx): engine.__name__
                   for engine in _SEARCH_ENGINES}
        for future in as_completed(futures):
            try:
                engine_results = future.result()
                all_results.extend(engine_results)
            except Exception:
                pass

    if not all_results:
        return "(no results found)"

    # ── Deduplicate by URL domain + title similarity ──
    seen = set()
    merged = []
    for r in all_results:
        url = r.get("url", "")
        title = r.get("title", "")
        if not url or not title:
            continue

        # Extract domain for dedup
        try:
            domain = urlparse(url).netloc.lower()
        except Exception:
            domain = url

        # Simple dedup key: domain + first 30 chars of title
        dedup_key = f"{domain}|{title[:30].lower()}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        # Domain filters
        if allowed_domains or blocked_domains:
            if blocked_domains and any(d.lower() in domain for d in blocked_domains):
                continue
            if allowed_domains and not any(d.lower() in domain for d in allowed_domains):
                continue

        merged.append(r)

    # ── Format output ──
    lines = []
    for i, r in enumerate(merged[:MAX_TOTAL_RESULTS]):
        engine_tag = f"[{r['engine']}]" if r['engine'] != "baidu" else ""
        line = f"{i+1}. {engine_tag} [{r['title']}]({r['url']})"
        if r.get("snippet"):
            line += f"\n   {r['snippet'][:200]}"
        lines.append(line)

    return "\n\n".join(lines)


# ── Web Fetch ──

def run_web_fetch(url: str, prompt: str = "") -> str:
    """Fetch a URL and extract readable text content."""
    httpx = _get_httpx()
    if not httpx:
        return "Error: 'httpx' package not installed. Run: pip install httpx"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        with httpx.stream(
            "GET", url,
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "").lower()
            if "text" not in ct and "html" not in ct and "xml" not in ct:
                return f"Error: unsupported content-type '{ct}'"

            chunks = []
            total = 0
            for chunk in resp.iter_bytes(chunk_size=8192):
                if chunk:
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= MAX_FETCH_SIZE:
                        break

        raw = b"".join(chunks)
        encoding = resp.encoding or "utf-8"
        try:
            html = raw.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            html = raw.decode("utf-8", errors="replace")

        text = _extract_text(html)

        header = f"URL: {url}\n"
        if prompt:
            header += f"Context: {prompt}\n"
        header += f"Content ({len(text)} chars):\n\n"
        return header + text

    except Exception as e:
        return f"Error fetching {url}: {type(e).__name__}: {e}"
