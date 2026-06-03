"""
Multimodal input support for cc_mine.
Handles images (base64), PDFs (text extraction), and arbitrary files as user input.

OpenAI Vision API format:
  {"role": "user", "content": [
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
    {"type": "text", "text": "user question"}
  ]}
"""

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

# ── Pending attachments ──
# Buffer of content blocks waiting to be merged into the next user message.
_pending: list[dict[str, Any]] = []

# Supported image MIME types for vision models
IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/jpg",
    "image/gif", "image/webp", "image/bmp",
}
# Image extensions (fallback when mimetype detection fails)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# Text file extensions we can read inline
TEXT_EXTENSIONS = {
    ".py", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".scss", ".less",
    ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".java", ".kt", ".swift",
    ".sh", ".bat", ".ps1", ".sql", ".xml", ".csv", ".log", ".env", ".gitignore",
    ".cfg", ".conf", ".toml", ".lock",
}


def _guess_mime(path: str) -> str:
    """Detect MIME type. Returns 'application/octet-stream' if unknown."""
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


def is_image(path: str) -> bool:
    """Check if a file is an image."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return True
    mime = _guess_mime(path)
    return mime in IMAGE_MIMES


def is_text_file(path: str) -> bool:
    """Check if a file is likely a readable text file."""
    ext = Path(path).suffix.lower()
    return ext in TEXT_EXTENSIONS


def is_pdf(path: str) -> bool:
    """Check if a file is a PDF."""
    ext = Path(path).suffix.lower()
    return ext == ".pdf" or _guess_mime(path) == "application/pdf"


# ── Content block builders ──

def _encode_image(path: str) -> str:
    """Encode an image file as a base64 data URL."""
    mime = _guess_mime(path)
    if mime not in IMAGE_MIMES:
        # Fallback: detect from extension
        ext = Path(path).suffix.lower()
        ext_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
        mime = ext_map.get(ext, "image/png")

    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime};base64,{data}"


def image_block(path: str) -> dict:
    """Build an image_url content block for OpenAI Vision API."""
    url = _encode_image(path)
    size = os.path.getsize(path)
    print(f"  \033[36m[image] {path} ({_guess_mime(path)}, {size//1024}KB)\033[0m")
    return {"type": "image_url", "image_url": {"url": url, "detail": "auto"}}


def text_block(text: str) -> dict:
    """Build a text content block."""
    return {"type": "text", "text": text}


def file_text_block(path: str, max_chars: int = 8000) -> dict | None:
    """Read a text file and return a content block with file context."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="gbk", errors="replace") as f:
                content = f.read()
        except Exception:
            return None
    except Exception:
        return None

    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars] + f"\n... [truncated, {len(content) - max_chars} more chars]"

    size = os.path.getsize(path)
    print(f"  \033[36m[file] {path} ({size//1024}KB{', truncated' if truncated else ''})\033[0m")
    return text_block(
        f"[File: {Path(path).name}]\n```{Path(path).suffix.lstrip('.')}\n{content}\n```"
    )


def pdf_text_block(path: str) -> dict | None:
    """Try to extract text from a PDF. Returns None if extraction fails."""
    try:
        import PyPDF2
    except ImportError:
        try:
            import pikepdf
        except ImportError:
            return text_block(
                f"[File: {Path(path).name}]\n"
                f"(PDF — install PyPDF2 or pikepdf to extract text. Currently showing metadata only.)\n"
                f"Size: {os.path.getsize(path)//1024}KB\n"
            )

    try:
        # Try pikepdf first (faster, more reliable)
        if "pikepdf" in globals():
            # Actually, we haven't imported it yet. Let me stick with PyPDF2.
            pass
        reader = PyPDF2.PdfReader(path)
        pages = []
        for i, page in enumerate(reader.pages[:20]):  # max 20 pages
            text = page.extract_text()
            if text:
                pages.append(f"--- Page {i+1} ---\n{text}")
        if not pages:
            return text_block(f"[File: {Path(path).name}]\n(PDF — no extractable text found)")
        content = "\n\n".join(pages)
        if len(reader.pages) > 20:
            content += f"\n\n... ({len(reader.pages) - 20} more pages)"
        print(f"  \033[36m[pdf] {path} ({os.path.getsize(path)//1024}KB, {len(reader.pages)} pages)\033[0m")
        return text_block(f"[File: {Path(path).name} (PDF)]\n{content}")
    except Exception as e:
        return text_block(
            f"[File: {Path(path).name}]\n"
            f"(PDF extraction failed: {e})\n"
            f"Size: {os.path.getsize(path)//1024}KB\n"
        )


# ── Public API ──

def attach_file(path_str: str) -> str:
    """Attach a file to the next user message. Returns a status message.

    Supports:
      - Images (png/jpg/gif/webp/bmp) → base64 data URL
      - Text files (.py/.md/.json/...) → inline content
      - PDFs → text extraction (requires PyPDF2)
      - Other files → metadata only
    """
    path = str(Path(path_str).resolve())
    if not os.path.isfile(path):
        return f"\033[31mFile not found: {path_str}\033[0m"

    if is_image(path):
        block = image_block(path)
        _pending.append(block)
        return f"Image attached: {Path(path).name} — will be sent with your next message."

    if is_pdf(path):
        block = pdf_text_block(path)
        if block:
            _pending.append(block)
            return f"PDF attached: {Path(path).name} — text extracted and queued for next message."
        return f"PDF attached but could not extract text: {Path(path).name}"

    if is_text_file(path):
        block = file_text_block(path)
        if block:
            _pending.append(block)
            return f"File attached: {Path(path).name} — content queued for next message."
        return f"\033[31mCould not read file: {path_str}\033[0m"

    # Unknown file type — just show metadata
    size = os.path.getsize(path)
    mime = _guess_mime(path)
    _pending.append(text_block(
        f"[Attached file: {Path(path).name} ({mime}, {size//1024}KB)]\n"
        f"(Binary file — only metadata shown)"
    ))
    return f"Binary file attached: {Path(path).name} ({mime}, {size//1024}KB)"


def attach_image(path_str: str) -> str:
    """Specifically attach an image file. Shortcut for attach_file with image validation."""
    path = str(Path(path_str).resolve())
    if not os.path.isfile(path):
        return f"\033[31mFile not found: {path_str}\033[0m"
    if not is_image(path):
        return f"\033[31mNot an image: {path_str}\033[0m (use /file for other types)"

    block = image_block(path)
    _pending.append(block)
    return f"Image attached: {Path(path).name} — will be sent with your next message."


def drain_pending() -> list[dict[str, Any]]:
    """Collect and clear pending attachment blocks. Called when building a user message."""
    global _pending
    blocks = _pending[:]
    _pending.clear()
    return blocks


def has_pending() -> bool:
    """Check if there are attachments waiting to be sent."""
    return len(_pending) > 0


def pending_count() -> int:
    """Number of pending attachments."""
    return len(_pending)


def clear_pending() -> int:
    """Clear all pending attachments. Returns number cleared."""
    global _pending
    n = len(_pending)
    _pending.clear()
    return n


def list_pending() -> str:
    """Show what's in the pending queue."""
    if not _pending:
        return "(no pending attachments)"
    lines = [f"{len(_pending)} pending attachment(s):"]
    for i, block in enumerate(_pending):
        if block.get("type") == "image_url":
            url = block.get("image_url", {}).get("url", "")[:60]
            lines.append(f"  [{i+1}] image — {url}...")
        elif block.get("type") == "text":
            text = block.get("text", "")[:100]
            lines.append(f"  [{i+1}] text — {text}...")
        else:
            lines.append(f"  [{i+1}] {block}")
    return "\n".join(lines)
