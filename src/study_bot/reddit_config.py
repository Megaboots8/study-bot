"""Load reddit_posts.yml and expose REDDIT_POSTS.

REDDIT_POSTS is a dict keyed by the exact survey label (matching SURVEYS in
config.py).  Each value is the corresponding YAML mapping with at minimum:
    submit_url: str
    title:      str

If reddit_posts.yml is absent the dict is empty and the Reddit pre-fill step
is silently skipped for all surveys.
"""

from __future__ import annotations

from pathlib import Path

_YAML_PATH = Path(__file__).resolve().parents[2] / "reddit_posts.yml"


def _load() -> dict[str, dict]:
    if not _YAML_PATH.exists():
        return {}
    try:
        import yaml  # imported lazily so pyyaml is only required when the file exists
        data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8")) or {}
        return data.get("posts", {})
    except Exception as exc:
        import warnings
        warnings.warn(f"Could not load reddit_posts.yml: {exc}", stacklevel=2)
        return {}


REDDIT_POSTS: dict[str, dict] = _load()
