"""Open a new browser tab on Reddit's submit page with the title pre-filled.

Uses Reddit's officially-supported URL parameters (`?title=...&url=...`,
`?title=...&text=...`), which are the same mechanism every "Share to
Reddit" button on the web uses.  No browser automation, no profile
management, no automation fingerprints.

Auto-flair / auto-post flow
----------------------------
When the YAML entry sets `auto_click_add: true` and a `flair` is
configured, the companion Tampermonkey userscript
(`userscripts/reddit-auto-flair.user.js`, v0.8+) runs inside the new tab
and drives the full submission sequence:

1. The userscript opens the flair dialog and selects the correct radio.
2. It locates each target button via DOM (immune to color, zoom, sidebar
   clutter) and encodes its exact physical-pixel screen position in
   document.title as `[SBP:<phase>:<x>,<y>]`.
3. Python (`reddit_click.py`) polls the foreground window title, reads the
   coordinates, and fires one OS-level pyautogui.click per phase.  OS
   clicks have isTrusted=true, which Reddit's Lit components require.

If `auto_post: true` is also set, the URL includes `_autopost=true` so the
userscript continues through the Post and (optional) Submit-without-editing
phases automatically.

See `reddit_click.py` for the full phase-IPC protocol.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
import webbrowser
from typing import Literal

logger = logging.getLogger(__name__)

PostType = Literal["link", "text"]


def prefill(
    subreddit: str,
    title: str,
    post_type: PostType = "link",
    link_url: str = "",
    body: str = "",
    flair: str = "",
    auto_click_add: bool = False,
    auto_post: bool = False,
) -> None:
    """Open a new browser tab to Reddit's submit form, pre-filled.

    Parameters
    ----------
    subreddit:
        Subreddit name without the `r/` prefix (e.g. "SampleSize").
    title:
        Post title.
    post_type:
        "link" (default) opens the Link tab and pre-fills `link_url`.
        "text" opens the Text tab and optionally pre-fills `body`.
    link_url:
        URL placed in the Link URL field when `post_type == "link"`.
    body:
        Optional self-text body.
    flair:
        Flair name.  When set, the URL carries `_autoflair=<name>` so
        the userscript can select the matching radio option.
    auto_click_add:
        When True (and flair is set), Python waits for the userscript to
        signal `[SBP:add:x,y]` and OS-clicks the flair Add button.
    auto_post:
        When True (and auto_click_add is True), the URL also carries
        `_autopost=true` so the userscript continues through the Post and
        Submit-without-editing phases.  Python OS-clicks each in turn.
    """
    try:
        params: dict[str, str] = {"title": title}

        if post_type == "link":
            params["type"] = "LINK"
            if link_url:
                params["url"] = link_url
            if body:
                params["text"] = body
        else:
            params["type"] = "TEXT"
            if body:
                params["selftext"] = "true"
                params["text"] = body

        if flair:
            params["_autoflair"] = flair
        if flair and auto_click_add and auto_post:
            params["_autopost"] = "true"

        qs = urllib.parse.urlencode(params)
        url = f"https://www.reddit.com/r/{subreddit}/submit?{qs}"
        webbrowser.open_new_tab(url)
        logger.info(
            "Reddit pre-fill: opened %s submit tab for r/%s",
            post_type, subreddit,
        )
    except Exception as exc:
        logger.warning("Reddit pre-fill failed: %s", exc)
        return

    # Reset Chrome's zoom to 100% (Ctrl+0) so that the CSS-pixel coordinates
    # the userscript computes via getBoundingClientRect map cleanly to
    # physical pixels (devicePixelRatio × 1 at 100% browser zoom).
    # We wait for Chrome to bring the new tab to front before sending the
    # keystroke.
    try:
        import pyautogui as _pag
        from .reddit_click import _foreground_window_title, _is_reddit_foreground
        time.sleep(2.0)
        _ftitle = _foreground_window_title()
        if _is_reddit_foreground(_ftitle):
            _pag.FAILSAFE = False
            _pag.hotkey("ctrl", "0")
            logger.info("Sent Ctrl+0 to reset Reddit tab zoom to 100%%")
        else:
            logger.debug(
                "Foreground window is %r; skipping zoom reset",
                _ftitle,
            )
    except Exception as exc:
        logger.debug("Zoom reset skipped: %s", exc)

    if not (flair and auto_click_add):
        return

    try:
        from .reddit_click import (
            click_add_button,
            click_post_button,
            click_submit_without_editing,
        )
    except Exception as exc:
        logger.warning("Could not import reddit_click helpers: %s", exc)
        return

    # --- Phase 1: flair Add ---
    try:
        added = click_add_button()
    except Exception as exc:
        logger.warning("Auto-click Add failed: %s", exc)
        return

    if not (added and auto_post):
        return

    # Give the flair dialog a moment to fully disappear before the
    # userscript emits [SBP:post].
    time.sleep(0.5)

    # --- Phase 2: Post ---
    try:
        posted = click_post_button()
    except Exception as exc:
        logger.warning("Auto-click Post failed: %s", exc)
        return

    if not posted:
        return

    # --- Phase 3: Submit without editing (optional — warning dialog) ---
    try:
        click_submit_without_editing()
    except Exception as exc:
        logger.warning("Auto-click Submit-without-editing failed: %s", exc)
