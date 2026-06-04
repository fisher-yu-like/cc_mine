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
DDG_API_URL = "https://api.duckduckgo.com/"  # DuckDuckGo Instant Answer API


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
    """Search the web using DuckDuckGo Instant Answer API and return top results."""
    httpx = _get_httpx()
    if not httpx:
        return "Error: 'httpx' package not installed. Run: pip install httpx"

    try:
        resp = httpx.get(
            DDG_API_URL,
            params={"q": query, "format": "json", "no_html": "1"},
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Error searching: {type(e).__name__}: {e}"

    results = []
    seen_urls = set()

    def _add_result(url: str, title: str, snippet: str = ""):
        """Add a result entry if URL is new and passes domain filters."""
        if not url or url in seen_urls:
            return
        # Apply domain filters
        if allowed_domains or blocked_domains:
            domain = urlparse(url).netloc.lower()
            if blocked_domains and any(d.lower() in domain for d in blocked_domains):
                return
            if allowed_domains and not any(d.lower() in domain for d in allowed_domains):
                return
        seen_urls.add(url)
        title = _html_lib.unescape(title.strip())
        snippet = _html_lib.unescape(snippet.strip()) if snippet else ""
        idx = len(results) + 1
        line = f"{idx}. [{title}]({url})"
        if snippet:
            line += f"\n   {snippet}"
        results.append(line)

    # 1. Abstract (instant answer) — if present
    abstract = (data.get("Abstract") or "").strip()
    abstract_url = (data.get("AbstractURL") or "").strip()
    heading = (data.get("Heading") or "").strip()
    if abstract:
        title = heading or abstract_url or "Abstract"
        _add_result(abstract_url or DDG_API_URL, title, abstract)

    # 2. RelatedTopics
    for topic in data.get("RelatedTopics") or []:
        if isinstance(topic, dict):
            text = (topic.get("Text") or "").strip()
            url = (topic.get("FirstURL") or "").strip()
            if text:
                # Text often is "Title — snippet"
                if " — " in text:
                    parts = text.split(" — ", 1)
                    title = parts[0].strip()
                    snippet = parts[1].strip()
                else:
                    title = text
                    snippet = ""
                _add_result(url, title, snippet)

    # 3. Results field (if present)
    for item in data.get("Results") or []:
        if isinstance(item, dict):
            url = (item.get("FirstURL") or "").strip()
            text = (item.get("Text") or "").strip()
            if text:
                if " — " in text:
                    parts = text.split(" — ", 1)
                    title = parts[0].strip()
                    snippet = parts[1].strip()
                else:
                    title = text
                    snippet = ""
                _add_result(url, title, snippet)

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
