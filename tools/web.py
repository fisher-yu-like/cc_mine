"""
Web search and fetch tools for cc_mine.
- web_search: query a search engine, return top results
- web_fetch: fetch a URL and extract readable text
"""

import re
import html as _html_lib
from html.parser import HTMLParser
from urllib.parse import urlparse

_requests = None  # lazy-loaded when needed

# ── Configuration ──
MAX_FETCH_SIZE = 100 * 1024     # 100KB max HTML download
MAX_FETCH_TEXT = 8000            # chars of extracted text returned
FETCH_TIMEOUT = 15               # seconds
USER_AGENT = (
    "Mozilla/5.0 (compatible; cc_mine/1.0; +https://github.com/cc_mine)"
)
SEARCH_URL = "https://lite.duckduckgo.com/lite/"  # no-JS lightweight search


def _get_requests():
    global _requests
    if _requests is None:
        try:
            import requests
            _requests = requests
        except ImportError:
            return None
    return _requests


def _has_requests() -> bool:
    return _get_requests() is not None


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
        # Collapse whitespace
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
    """Search the web and return top results."""
    requests = _get_requests()
    if not requests:
        return "Error: 'requests' package not installed. Run: pip install requests"

    try:
        resp = requests.post(
            SEARCH_URL,
            data={"q": query, "kl": "us-en"},
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as e:
        return f"Error searching: {type(e).__name__}: {e}"

    # Extract result links from DuckDuckGo Lite HTML
    results = []
    link_pattern = re.compile(
        r'<a[^>]*class="result-link"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    snippet_pattern = re.compile(
        r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
        re.DOTALL | re.IGNORECASE,
    )

    links = link_pattern.findall(resp.text)
    snippets = snippet_pattern.findall(resp.text)

    for i, (url, title) in enumerate(links[:8]):
        url = _html_lib.unescape(url.strip())
        title = re.sub(r'<[^>]+>', '', title).strip()
        title = _html_lib.unescape(title)

        # Apply domain filters
        if allowed_domains or blocked_domains:
            domain = urlparse(url).netloc.lower()
            if blocked_domains and any(d.lower() in domain for d in blocked_domains):
                continue
            if allowed_domains and not any(d.lower() in domain for d in allowed_domains):
                continue

        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
            snippet = _html_lib.unescape(snippet)

        results.append(f"{i+1}. [{title}]({url})\n   {snippet}")

    if not results:
        return "(no results found)"
    return "\n\n".join(results)


# ── Web Fetch ──
def run_web_fetch(url: str, prompt: str = "") -> str:
    """Fetch a URL and extract readable text content."""
    requests = _get_requests()
    if not requests:
        return "Error: 'requests' package not installed. Run: pip install requests"

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT,
            stream=True,
            allow_redirects=True,
        )
        resp.raise_for_status()

        # Check content-type — only fetch HTML/text
        ct = resp.headers.get("content-type", "").lower()
        if "text" not in ct and "html" not in ct and "xml" not in ct:
            return f"Error: unsupported content-type '{ct}' for text extraction"

        # Read up to MAX_FETCH_SIZE
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192, decode_unicode=False):
            if chunk:
                chunks.append(chunk)
                total += len(chunk)
                if total >= MAX_FETCH_SIZE:
                    break
        resp.close()

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
