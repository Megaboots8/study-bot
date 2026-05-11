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

Return value
------------
`prefill()` returns a `PostResult` dataclass so callers can build a
combined Telegram message that includes both the count check result and
whether the post actually went up.

See `reddit_click.py` for the full phase-IPC protocol.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from typing import Literal, Optional

logger = logging.getLogger(__name__)

PostType = Literal["link", "text"]

# Phase status constants.
_OK = "ok"
_SKIPPED = "skipped"
_TIMEOUT = "timeout"
_ERROR = "error"
_NOT_NEEDED = "not_needed"


@dataclass
class PostResult:
    """Outcome of a single `prefill()` call.

    Fields
    ------
    tab_opened : bool
        True if `webbrowser.open_new_tab` succeeded.
    add_phase : str
        "ok" | "skipped" (no flair / no auto_click_add) | "timeout" | "error"
    post_phase : str
        "ok" | "skipped" (auto_post not requested) | "timeout" | "error"
    submit_phase : str
        "ok" (Submit-without-editing was clicked) |
        "not_needed" (post went through without warning dialog) |
        "skipped" | "timeout" | "error"
    success : bool
        True when the post flow completed as far as was requested:
        tab opened AND each requested phase succeeded or was not needed.
    reason : str
        Short human-readable summary, empty string on full success.
    dry_run : bool
        True when the caller passed dry_run=True.
    """
    tab_opened: bool = False
    add_phase: str = _SKIPPED
    post_phase: str = _SKIPPED
    submit_phase: str = _SKIPPED
    success: bool = False
    reason: str = ""
    dry_run: bool = False

    def summary_line(self) -> str:
        """One-line human-readable summary for Telegram / logs."""
        if self.dry_run:
            return "SKIPPED (dry-run)"
        if not self.tab_opened:
            return f"FAILED — {self.reason}"
        if self.success:
            return "OK"
        return f"FAILED — {self.reason}"


def prefill(
    subreddit: str,
    title: str,
    post_type: PostType = "link",
    link_url: str = "",
    body: str = "",
    flair: str = "",
    auto_click_add: bool = False,
    auto_post: bool = False,
    dry_run: bool = False,
    stop_event: Optional[threading.Event] = None,
) -> PostResult:
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
    dry_run:
        When True, opens the tab so the operator can inspect the pre-filled
        content, but does NOT auto-click anything.  Returns a PostResult
        with success=True and dry_run=True.
    stop_event:
        Optional threading.Event; when set, each phase poll loop exits early.

    Returns
    -------
    PostResult
        Structured outcome that callers use to build a Telegram message.
    """
    result = PostResult(dry_run=dry_run)

    # --- Open the tab ---
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
        if flair and auto_click_add and auto_post and not dry_run:
            params["_autopost"] = "true"

        qs = urllib.parse.urlencode(params)
        url = f"https://www.reddit.com/r/{subreddit}/submit?{qs}"
        webbrowser.open_new_tab(url)
        result.tab_opened = True
        logger.info(
            "Reddit pre-fill: opened %s submit tab for r/%s",
            post_type, subreddit,
        )
    except Exception as exc:
        result.tab_opened = False
        result.reason = f"tab open failed: {exc}"
        logger.warning("Reddit pre-fill failed: %s", exc)
        return result

    # --- Dry-run: tab is open for inspection, nothing to click ---
    if dry_run:
        result.success = True
        return result

    # --- Reset Chrome's zoom to 100% (Ctrl+0) ---
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
            # Reddit might not be foreground yet; try to bring it up.
            from .reddit_click import bring_reddit_to_foreground
            if bring_reddit_to_foreground():
                _pag.FAILSAFE = False
                _pag.hotkey("ctrl", "0")
                logger.info("Sent Ctrl+0 after bring-to-foreground")
            else:
                logger.debug(
                    "Foreground window is %r; skipping zoom reset",
                    _ftitle,
                )
    except Exception as exc:
        logger.debug("Zoom reset skipped: %s", exc)

    # --- No auto-clicking requested ---
    if not (flair and auto_click_add):
        result.add_phase = _SKIPPED
        result.post_phase = _SKIPPED
        result.submit_phase = _SKIPPED
        result.success = True
        return result

    try:
        from .reddit_click import (
            click_add_button,
            click_post_button,
            click_submit_without_editing,
        )
    except Exception as exc:
        result.add_phase = _ERROR
        result.reason = f"import reddit_click failed: {exc}"
        logger.warning("Could not import reddit_click helpers: %s", exc)
        return result

    # --- Phase 1: flair Add ---
    try:
        added = click_add_button(stop_event=stop_event)
    except Exception as exc:
        result.add_phase = _ERROR
        result.reason = f"Add click error: {exc}"
        logger.warning("Auto-click Add failed: %s", exc)
        return result

    if not added:
        result.add_phase = _TIMEOUT
        result.reason = "Add button not clicked (Reddit window not foreground or timeout)"
        return result

    result.add_phase = _OK

    # --- No auto-post requested ---
    if not auto_post:
        result.post_phase = _SKIPPED
        result.submit_phase = _SKIPPED
        result.success = True
        return result

    # Give the flair dialog a moment to fully disappear before the
    # userscript emits [SBP:post].
    time.sleep(0.5)

    # --- Phase 2: Post ---
    try:
        posted = click_post_button(stop_event=stop_event)
    except Exception as exc:
        result.add_phase = _OK
        result.post_phase = _ERROR
        result.reason = f"Post click error: {exc}"
        logger.warning("Auto-click Post failed: %s", exc)
        return result

    if not posted:
        result.post_phase = _TIMEOUT
        result.reason = "Post button not clicked (timeout)"
        return result

    result.post_phase = _OK

    # --- Phase 3: Submit without editing (optional — warning dialog) ---
    try:
        submitted = click_submit_without_editing(stop_event=stop_event)
    except Exception as exc:
        result.submit_phase = _ERROR
        result.reason = f"Submit-without-editing click error: {exc}"
        logger.warning("Auto-click Submit-without-editing failed: %s", exc)
        # The post was already clicked; treat as success (Reddit may have
        # accepted it without the warning dialog).
        result.success = True
        return result

    result.submit_phase = _OK if submitted else _NOT_NEEDED
    result.success = True
    return result
