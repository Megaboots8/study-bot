"""Load reddit_posts.yml and expose REDDIT_POSTS + REDDIT_SETTINGS.

REDDIT_POSTS is a dict keyed by the exact survey/experiment label
(matching SURVEYS / EXPERIMENTS in config.py).  Each value is either a
single post mapping OR a list of post mappings (a single survey/experiment
can post to several subreddits with different schedules).

REDDIT_SETTINGS is a dict of top-level options:
    jitter_seconds: int — uniform random offset applied to each scheduled
                          post fire time, in [-jitter_seconds, +jitter_seconds].
                          0 disables jitter.  Defaults to 120.

If reddit_posts.yml is absent, REDDIT_POSTS is empty and REDDIT_SETTINGS
holds the defaults.  The Reddit-pre-fill step is silently skipped for any
survey / experiment with no entry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_YAML_PATH = Path(__file__).resolve().parents[2] / "reddit_posts.yml"

_DEFAULT_SETTINGS: dict[str, Any] = {
    "jitter_seconds": 120,
}


def _load() -> tuple[dict[str, Any], dict[str, Any]]:
    if not _YAML_PATH.exists():
        return {}, dict(_DEFAULT_SETTINGS)
    try:
        import yaml
        data = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8")) or {}
        posts = data.get("posts", {})
        settings = dict(_DEFAULT_SETTINGS)
        if "jitter_seconds" in data:
            try:
                settings["jitter_seconds"] = int(data["jitter_seconds"])
            except (TypeError, ValueError):
                import warnings
                warnings.warn(
                    f"reddit_posts.yml: invalid jitter_seconds {data['jitter_seconds']!r}, "
                    f"using default {_DEFAULT_SETTINGS['jitter_seconds']}",
                    stacklevel=3,
                )
        return posts, settings
    except Exception as exc:
        import warnings
        warnings.warn(f"Could not load reddit_posts.yml: {exc}", stacklevel=2)
        return {}, dict(_DEFAULT_SETTINGS)


REDDIT_POSTS, REDDIT_SETTINGS = _load()
