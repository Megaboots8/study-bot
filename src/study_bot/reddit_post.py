"""Open a new browser tab on Reddit's submit page with the title pre-filled.

Uses Reddit's officially-supported URL parameters (`?title=...&url=...`,
`?title=...&text=...`), which are the same mechanism every "Share to
Reddit" button on the web uses.  No browser automation, no profile
management, no automation fingerprints.

The new tab opens in your already-running default browser (Chrome),
where you are already logged in.  Review the post and click "Post" by
hand.

If the YAML entry sets `auto_click_add: true` and a `flair` is
configured, study-bot will additionally take screenshots a few seconds
after opening the tab and issue an OS-level mouse click on the flair
dialog's blue "Add" button.  See `reddit_click.py` for why that step
needs to happen at the OS level rather than from the userscript.
"""

from __future__ import annotations

import logging
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
) -> None:
    """Open a new browser tab to Reddit's submit form, pre-filled.

    Parameters
    ----------
    subreddit:
        The subreddit name without the `r/` prefix (e.g. "SampleSize").
    title:
        Post title — fills the Title field on either tab.
    post_type:
        "link" (default) opens the Link tab and pre-fills `link_url`.
        "text" opens the Text tab and (optionally) pre-fills `body`.
    link_url:
        URL placed in the Link URL field when `post_type == "link"`.
        Ignored for text posts.
    body:
        Optional self-text body.  Used as the post body for text posts
        and as the optional body field for link posts.
    flair:
        Optional flair name.  When set, the URL also carries a custom
        `_autoflair=<name>` query parameter that Reddit ignores but the
        companion Tampermonkey userscript at
        `userscripts/reddit-auto-flair.user.js` reads and uses to open
        the flair dialog and select the matching radio option.
    auto_click_add:
        When True (and a flair is configured), watch the screen for the
        dialog's blue "Add" button and issue an OS-level mouse click on
        it once it appears.  Required because Reddit's Lit-based dialog
        rejects the synthetic click the userscript would otherwise issue.
        This blocks for up to ~15 seconds.

    Never raises; failures are logged as warnings so the rest of
    study-bot keeps running.
    """
    try:
        params: dict[str, str] = {"title": title}

        if post_type == "link":
            params["type"] = "LINK"
            if link_url:
                params["url"] = link_url
            if body:
                # Reddit's link-post form has an optional body field.
                params["text"] = body
        else:
            params["type"] = "TEXT"
            if body:
                params["selftext"] = "true"
                params["text"] = body

        if flair:
            params["_autoflair"] = flair

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

    if flair and auto_click_add:
        try:
            from .reddit_click import click_add_button
            click_add_button()
        except Exception as exc:
            logger.warning("Auto-click Add failed: %s", exc)
