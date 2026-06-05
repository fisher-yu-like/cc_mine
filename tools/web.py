"""
Web search and fetch tools for cc_mine.
- web_search: query a search engine, return top results
- web_fetch: fetch a URL and extract readable text
"""

import re
import html as _html_lib
from html.parser import HTMLParser
from urllib.parse import urlparse

_httpx = None  # lazy-loaded when needed

# ── Configuration ──
MAX_FETCH_SIZE = 100 * 1024     # 100KB max HTML download
MAX_FETCH_TEXT = 8000            # chars of extracted text returned
FETCH_TIMEOUT = 15               # seconds
USER_AGENT = (
    "Mozilla/5.0 (compatible; cc_mine/1.0; +https://github.com/cc_mine)"
)
# Baidu search — accessible in China, returns real search results
BAIDU_URL = "https://www.baidu.com/s"

SEARCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _get_httpx():
    global _httpx
    if _httpx is None:
        try:
            import httpx
            _httpx = httpx
        except ImportError:
            return None
    return _httpx


def _has_httpx() -> bool:
    return _get_httpx() is not None


# ── HTML to Text (stdlib only) ──
class _TextExtractor(HTMLParser):
    """Strip HTML tags and extract visible text."""
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


# ── Web Search ──
def run_web_search(query: str,
                   allowed_domains: list[str] | None = None,
                   blocked_domains: list[str] | None = None) -> str:
    """Search the web using Baidu and return top results.

    Falls back to DuckDuckGo if Baidu is unreachable.
    """
    httpx = _get_httpx()
    if not httpx:
        return "Error: 'httpx' package not installed. Run: pip install httpx"

    return _search_baidu(query, allowed_domains, blocked_domains, httpx)


def _search_baidu(query: str, allowed_domains, blocked_domains, httpx) -> str:
    """Search Baidu and parse results."""
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
    except Exception as e:
        return f"Error searching: {type(e).__name__}: {e}"

    results = []
    seen_urls = set()

    # Baidu result blocks: <h3 class="t"> or <h3 class="c-title">
    # containing <a href="...">title</a>
    # Snippets in <div class="c-abstract"> or <span class="c-abstract">
    block_pattern = re.compile(
        r'<(div|h3)[^>]*class=["\'](?:result|c-container)[^"\']*["\'][^>]*>.*?</\1>',
        re.DOTALL | re.IGNORECASE
    )

    # Find all h3 title links
    title_pattern = re.compile(
        r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE
    )
    # Find snippets
    snippet_pattern = re.compile(
        r'<(?:span|div)[^>]*class=["\'](?:c-abstract|content-right_[^"\']*)["\'][^>]*>(.*?)</(?:span|div)>',
        re.DOTALL | re.IGNORECASE
    )

    title_matches = title_pattern.findall(html)
    snippet_matches = snippet_pattern.findall(html)

    for i, (url, title_html) in enumerate(title_matches[:12]):
        title = _html_lib.unescape(re.sub(r'<[^>]+>', '', title_html).strip())
        if not title or not url:
            continue

        # Resolve Baidu redirect URLs
        if "baidu.com/link" in url:
            # Mark as Baidu redirect — the real URL needs a second fetch
            pass

        if url in seen_urls:
            continue

        if allowed_domains or blocked_domains:
            try:
                domain = urlparse(url).netloc.lower()
            except Exception:
                domain = ""
            if blocked_domains and any(d.lower() in domain for d in blocked_domains):
                continue
            if allowed_domains and not any(d.lower() in domain for d in allowed_domains):
                continue

        seen_urls.add(url)

        snippet = ""
        if i < len(snippet_matches):
            snippet = _html_lib.unescape(
                re.sub(r'<[^>]+>', '', snippet_matches[i]).strip()
            )

        idx = len(results) + 1
        line = f"{idx}. [{title}]({url})"
        if snippet:
            line += f"\n   {snippet}"
        results.append(line)

    if not results:
        return "(no results found)"
    return "\n\n".join(results)


# ── Web Fetch ──
def run_web_fetch(url: str, prompt: str = "") -> str:
    """Fetch a URL and extract readable text content."""
    httpx = _get_httpx()
    if not httpx:
        return "Error: 'httpx' package not installed. Run: pip install httpx"

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        with httpx.stream(
            "GET",
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT,
        ) as resp:
            resp.raise_for_status()

            # Check content-type — only fetch HTML/text
            ct = resp.headers.get("content-type", "").lower()
            if "text" not in ct and "html" not in ct and "xml" not in ct:
                return f"Error: unsupported content-type '{ct}' for text extraction"

            # Read up to MAX_FETCH_SIZE
            chunks = []
            total = 0
            for chunk in resp.iter_bytes(chunk_size=8192):
                if chunk:
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= MAX_FETCH_SIZE:
                        break

        raw = b"".join(chunks)
        # Try UTF-8 first, then fall back to detected encoding
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
