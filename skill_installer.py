"""
Skill installer for cc_mine.

Downloads skills from URLs and installs into the skills/ directory.
Supports GitHub repos, raw .md files, and .zip archives.

Usage:
    from skill_installer import install_skill_from_url
"""
import subprocess
import shutil
from pathlib import Path
from urllib.parse import urlparse


def install_skill_from_url(url: str, skill_name: str = "") -> str:
    """Download a skill from a URL and install into skills/.

    Returns a status message.
    """
    from config import SKILLS_DIR

    try:
        import requests
    except ImportError:
        return "Error: 'requests' package required. Run: pip install requests"

    parsed = urlparse(url)

    if parsed.netloc in ("github.com", "www.github.com"):
        return _install_from_github(url, skill_name, SKILLS_DIR)
    elif url.endswith(".md"):
        return _install_md_file(url, skill_name, SKILLS_DIR)
    elif url.endswith(".zip"):
        return _install_zip(url, skill_name, SKILLS_DIR)
    else:
        return (f"Unsupported URL format: {url}. "
                f"Supported: GitHub repos, .md files, .zip archives.")


def _install_from_github(url: str, name: str, skills_dir: Path) -> str:
    """Clone a GitHub repo as a skill (shallow clone)."""
    repo_name = name or url.rstrip("/").split("/")[-1].replace(".git", "")
    target = skills_dir / repo_name

    if target.exists():
        return f"Skill '{repo_name}' already exists at {target}"

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(target)],
            capture_output=True, timeout=60, text=True
        )
        if result.returncode == 0:
            if (target / "SKILL.md").exists():
                return f"Skill '{repo_name}' installed from GitHub at {target}"
            else:
                shutil.rmtree(target, ignore_errors=True)
                return f"Error: No SKILL.md found in repository {url}"
        return f"Error: git clone failed: {result.stderr[:200]}"
    except FileNotFoundError:
        return "Error: git not found. Install git or download manually."
    except Exception as e:
        return f"Error installing from GitHub: {e}"


def _install_md_file(url: str, name: str, skills_dir: Path) -> str:
    """Download a .md file as a skill."""
    import requests
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        return f"Error downloading: {e}"

    skill_name = name or Path(urlparse(url).path).stem
    target_dir = skills_dir / skill_name
    target_dir.mkdir(parents=True, exist_ok=True)

    (target_dir / "SKILL.md").write_text(resp.text, encoding="utf-8")
    return f"Skill '{skill_name}' installed from {url}"


def _install_zip(url: str, name: str, skills_dir: Path) -> str:
    """Download and extract a .zip archive as a skill."""
    import requests, zipfile, io, tempfile
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        return f"Error downloading: {e}"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        # Find SKILL.md inside the zip
        skill_md = next((n for n in zf.namelist() if n.endswith("SKILL.md")), None)
        if not skill_md:
            return f"Error: No SKILL.md found in zip archive"

        skill_name = name or Path(skill_md).parent.name or "installed-skill"
        target_dir = skills_dir / skill_name
        target_dir.mkdir(parents=True, exist_ok=True)

        zf.extractall(target_dir)
        return f"Skill '{skill_name}' installed from zip at {target_dir}"
