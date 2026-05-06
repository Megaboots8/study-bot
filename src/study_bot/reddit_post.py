"""Open a new browser tab on Reddit's submit page with the title pre-filled.

Uses Reddit's officially-supported URL parameters (`?title=...&text=...`),
which are the same mechanism every "Share to Reddit" button on the web uses.
No browser automation, no profile management, no automation fingerprints.

The new tab opens in your already-running default browser (Chrome), where
you are already logged in.  Review the post and click "Post" by hand.

If the YAML entry sets `auto_click_add: true` and a `flair` is configured,
study-bot will additionally take screenshots a few seconds after opening
the tab and issue an OS-level mouse click on the flair dialog's blue "Add"
button.  See `reddit_click.py` for why that step needs to happen at the
OS level rather than from the userscript.
"""

from __future__ import annotations

import logging
import urllib.parse
import webbrowser

logger = logging.getLogger(__name__)


def prefill(
    subreddit: str,
    title: str,
    body: str = "",
    flair: str = "",
    auto_click_add: bool = False,
) -> None:
    """Open a new browser tab to Reddit's submit form with title (and body) pre-filled.

    Parameters
    ----------
    subreddit:
        The subreddit name without the `r/` prefix (e.g. "SampleSize").
    title:
        Post title — fills the Title field.
    body:
        Optional self-text body.  Empty string by default.
    flair:
        Optional flair name.  When set, the URL also carries a custom
        `_autoflair=<name>` query parameter that Reddit ignores but the
        companion Tampermonkey userscript at userscripts/reddit-auto-flair.user.js
        reads and uses to open the flair dialog and select the matching
        radio option.
    auto_click_add:
        When True (and a flair is configured), watch the screen for the
        dialog's blue "Add" button and issue an OS-level mouse click on it
        once it appears.  Required because Reddit's Lit-based dialog
        rejects the synthetic click the userscript would otherwise issue.
        This blocks for up to ~15 seconds.

    Never raises; failures are logged as warnings so the rest of study-bot
    keeps running.
    """
    try:
        params = {"title": title}
        if body:
            params["selftext"] = "true"
            params["text"] = body
        if flair:
            params["_autoflair"] = flair
        qs = urllib.parse.urlencode(params)
        url = f"https://www.reddit.com/r/{subreddit}/submit?{qs}"
        webbrowser.open_new_tab(url)
        logger.info("Reddit pre-fill: opened submit tab for r/%s", subreddit)
    except Exception as exc:
        logger.warning("Reddit pre-fill failed: %s", exc)
        return

    if flair and auto_click_add:
        try:
            from .reddit_click import click_add_button
            click_add_button()
        except Exception as exc:
            logger.warning("Auto-click Add failed: %s", exc)
