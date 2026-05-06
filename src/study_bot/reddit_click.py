"""OS-level mouse click for the Reddit flair dialog's "Add" button.

Background
----------
Reddit's flair picker is a Lit-based form-associated custom element
(`<faceplate-radio-input>` inside `<faceplate-dialog>`).  Synthetic
JavaScript click events visually select a radio option but do not
reliably update the component's internal form state, so the dialog's
"Add" button stays a no-op when triggered from a userscript.

What this module does
---------------------
The companion userscript (`userscripts/reddit-auto-flair.user.js`) opens
the flair dialog and selects the right radio option but stops short of
clicking "Add".  This module then takes a real screenshot, finds the
saturated-blue button-shaped region in the lower half of the screen, and
fires an OS-level mouse click on its centroid via pyautogui.  Because the
event comes from the operating system, Reddit's components see
`isTrusted=true` and accept the click.

Why connected components
------------------------
Naively checking the bounding box of *all* blue pixels in the lower half
of the screen breaks on screens that have unrelated blue UI in the same
region (taskbar icons, Reddit brand-blue accents, etc.).  The Add button
itself is one solid blue blob ~80x35 px; everything else is a different
disconnected blob.  We label each connected blob separately and pick the
largest one whose bounding box is button-shaped.

Safety
------
* Searches only the lower half of the primary monitor — the dialog's Add
  button is always near the bottom of the dialog when the dialog is
  centered, while the "Academic (Repost)" pill is in the upper portion of
  the dialog.  This avoids confusing the two blue regions.
* Requires the matched blob to be button-shaped (size + bounding-box
  aspect bounded).  If no blob meets the shape constraints, the click is
  skipped entirely.
* Re-checks the centroid is stable across two consecutive screenshots
  before clicking, so we never click during a fade-in animation.
* On failure (timeout reached without a click), saves the bottom-half
  screenshot and the blue-pixel mask to logs/ so the issue can be
  inspected.
* Best-effort throughout: any failure (missing dependency, no blob
  found, pyautogui failsafe triggered) is logged and the function
  returns False without raising.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# --- Color range that matches Reddit's primary-blue button (button-primary)
# Tuned against an actual screenshot of r/SampleSize's flair dialog: the
# Add button samples as RGB(10, 68, 155) = #0a449b.  Bounds are widened
# so antialiased edges and minor theme/zoom variations still match.
_BLUE_R_MAX = 80
_BLUE_G_MIN = 50
_BLUE_G_MAX = 180
_BLUE_B_MIN = 140
_BLUE_B_MAX = 250

# --- Acceptable button-blob shape ranges, in screen pixels.
# Reddit's flair-dialog Add button samples at ~100x79 px in full-screen
# captures of an ultrawide monitor (the bounding-box is taller than the
# nominal CSS height because the button's drop shadow and antialiased
# edges leak into the blue mask).  We require a fairly tall blob so that
# thin horizontal UI strips (link underlines, notification banners,
# in-page progress bars, etc.) get rejected.
#
# We also require aspect ratio (w/h) <= 4: real buttons are roughly
# square or only mildly elongated, while page-spanning blue strips have
# very large w/h ratios.
_MIN_BLOB_PIXELS = 600
_MIN_BUTTON_W = 40
_MAX_BUTTON_W = 260
_MIN_BUTTON_H = 30
_MAX_BUTTON_H = 110
_MAX_BUTTON_ASPECT = 4.0

# --- Vertical positional gate so we never click on a Windows taskbar icon
# (or any other system-tray UI sitting at the very bottom of the screen).
# We deliberately do NOT gate by x: Chrome can be positioned anywhere
# horizontally — including the right edge of an ultrawide monitor — and
# the flair dialog is centered inside the Chrome window, not the screen.
# Disambiguating multiple blue blobs is handled by picking the largest
# one that is button-shaped, which is much stronger than a position check.
#
#   y_center must be in [H * Y_FRAC_MIN, H * Y_FRAC_MAX]
#
# Lower half + above the bottom 8% covers the dialog's Add button while
# excluding the Windows taskbar.
_Y_FRAC_MIN = 0.50
_Y_FRAC_MAX = 0.92

_DEBUG_DIR = Path(__file__).resolve().parents[2] / "logs"

# Substring matches for the foreground window's title so we only ever
# click when the user is plausibly looking at a Reddit submit page.
# Reddit's actual submit page title in Chrome is for example
# "Submit to r/SampleSize - Google Chrome" — note that this does NOT
# contain the literal word "reddit", so a "reddit" substring alone is
# insufficient.  We accept any of these markers (case-insensitive):
#   - "reddit"   (covers "Reddit - Dive into anything", etc.)
#   - "r/"       (covers any subreddit page, including the submit page)
_FOREGROUND_TITLE_NEEDLES = ("reddit", "r/")


def _foreground_window_title() -> Optional[str]:
    """Return the foreground window's title, or None if unavailable.

    Uses pygetwindow (which pyautogui already depends on) on Windows.
    Returns None on platforms or in environments where the foreground
    window cannot be determined.
    """
    try:
        import pyautogui
        win = pyautogui.getActiveWindow()
        if win is None:
            return None
        title = getattr(win, "title", "") or ""
        return str(title)
    except Exception as exc:
        logger.debug("Could not read foreground window title: %s", exc)
        return None


def _is_reddit_foreground(title: Optional[str]) -> bool:
    if not title:
        return False
    lower = title.lower()
    return any(needle in lower for needle in _FOREGROUND_TITLE_NEEDLES)


def _label_clusters(mask) -> List[dict]:
    """Group True pixels in `mask` into 4-connected components.

    Returns a list of cluster dicts:
        {count, x_min, x_max, y_min, y_max, cx, cy}

    Uses union-find over only the True pixels, so it is fast for sparse
    masks (which is our case: at most a few thousand blue pixels in a
    multi-megapixel image).  Pure numpy + Python — no scipy needed.
    """
    import numpy as np

    ys, xs = mask.nonzero()
    n = len(ys)
    if n == 0:
        return []

    ys_list = ys.tolist()
    xs_list = xs.tolist()
    pos_to_idx = {(y, x): i for i, (y, x) in enumerate(zip(ys_list, xs_list))}

    parent = list(range(n))

    def find(a: int) -> int:
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:
            parent[a], a = root, parent[a]
        return root

    for i in range(n):
        y = ys_list[i]
        x = xs_list[i]
        # Only check up + left neighbors; right + down get unioned when
        # those pixels are processed in turn.
        j = pos_to_idx.get((y - 1, x))
        if j is not None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)
        j = pos_to_idx.get((y, x - 1))
        if j is not None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)

    groups: defaultdict = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    out: List[dict] = []
    for indices in groups.values():
        idxs = np.asarray(indices)
        cy = ys[idxs]
        cx = xs[idxs]
        out.append({
            "count": int(len(indices)),
            "x_min": int(cx.min()), "x_max": int(cx.max()),
            "y_min": int(cy.min()), "y_max": int(cy.max()),
            "cx": float(cx.mean()),
            "cy": float(cy.mean()),
        })
    return out


def _save_debug_artifacts(bottom_img, mask) -> None:
    """Save the bottom-half screenshot and mask overlay to logs/ for inspection."""
    try:
        import numpy as np
        from PIL import Image
        _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        Image.fromarray(bottom_img).save(_DEBUG_DIR / f"reddit-add-debug-{ts}-screen.png")
        # Tint the mask red on a darkened copy of the screenshot so it's
        # easy to see which pixels matched the blue range.
        overlay = (bottom_img.astype("int32") // 2).astype("uint8")
        overlay[mask] = np.array([255, 0, 0], dtype="uint8")
        Image.fromarray(overlay).save(_DEBUG_DIR / f"reddit-add-debug-{ts}-mask.png")
        logger.info(
            "Saved debug screenshots to %s (look for reddit-add-debug-%s-*.png)",
            _DEBUG_DIR, ts,
        )
    except Exception as exc:
        logger.debug("Could not save debug artifacts: %s", exc)


def _find_add_button(save_debug_on_miss: bool = False) -> Optional[Tuple[int, int]]:
    """Return (x, y) screen coordinates of a button-shaped blue blob, or None."""
    try:
        import numpy as np
        from PIL import ImageGrab
    except ImportError as exc:
        logger.warning("Pillow/numpy not installed; cannot auto-click Add (%s)", exc)
        return None

    try:
        img = np.array(ImageGrab.grab())
    except Exception as exc:
        logger.warning("Could not grab screenshot: %s", exc)
        return None

    h, w = img.shape[:2]
    y_offset = h // 2
    bottom = img[y_offset:, :, :3]

    r = bottom[..., 0]
    g = bottom[..., 1]
    b = bottom[..., 2]
    mask = (
        (r <= _BLUE_R_MAX)
        & (g >= _BLUE_G_MIN) & (g <= _BLUE_G_MAX)
        & (b >= _BLUE_B_MIN) & (b <= _BLUE_B_MAX)
    )

    pixel_count = int(mask.sum())
    if pixel_count < _MIN_BLOB_PIXELS:
        return None

    clusters = _label_clusters(mask)
    if not clusters:
        return None

    # Vertical position gate (absolute screen coords).  The cluster's
    # centroid is in cropped (lower-half) coordinates, so add y_offset
    # before comparing to screen-relative bounds.
    y_min_screen = h * _Y_FRAC_MIN
    y_max_screen = h * _Y_FRAC_MAX

    button_clusters = []
    for c in clusters:
        cw = c["x_max"] - c["x_min"]
        ch = c["y_max"] - c["y_min"]
        cy_screen = c["cy"] + y_offset
        if c["count"] < _MIN_BLOB_PIXELS:
            continue
        if not (_MIN_BUTTON_W <= cw <= _MAX_BUTTON_W):
            continue
        if not (_MIN_BUTTON_H <= ch <= _MAX_BUTTON_H):
            continue
        if ch == 0 or cw / ch > _MAX_BUTTON_ASPECT:
            continue
        if not (y_min_screen <= cy_screen <= y_max_screen):
            continue
        button_clusters.append(c)

    if not button_clusters:
        # Log the largest few clusters so we can see what the screen looked like.
        clusters_sorted = sorted(clusters, key=lambda c: c["count"], reverse=True)[:5]
        logger.debug(
            "Found %d blue cluster(s) in lower half; none passed shape+position. Top 5: %s",
            len(clusters),
            [
                {
                    "px": c["count"],
                    "w": c["x_max"] - c["x_min"],
                    "h": c["y_max"] - c["y_min"],
                    "at": (int(c["cx"]), int(c["cy"]) + y_offset),
                }
                for c in clusters_sorted
            ],
        )
        if save_debug_on_miss:
            _save_debug_artifacts(bottom, mask)
        return None

    best = max(button_clusters, key=lambda c: c["count"])
    cx_screen = int(best["cx"])
    cy_screen = int(best["cy"]) + y_offset
    logger.debug(
        "Picked button cluster: pixels=%d, bbox=(%d,%d)-(%d,%d), centroid=(%d,%d)",
        best["count"],
        best["x_min"], best["y_min"],
        best["x_max"], best["y_max"],
        cx_screen, cy_screen,
    )
    return cx_screen, cy_screen


def click_add_button(
    timeout_seconds: float = 12.0,
    poll_interval: float = 0.4,
    initial_delay: float = 3.0,
) -> bool:
    """Find and click Reddit's flair-dialog "Add" button via OS mouse.

    Parameters
    ----------
    timeout_seconds:
        Total time budget (after the initial delay) to keep polling for
        the button before giving up.
    poll_interval:
        Seconds between screenshots while polling.
    initial_delay:
        Seconds to wait before the first screenshot, so the page and the
        userscript both have time to open the dialog and select the radio.

    Returns
    -------
    True if a click was issued, False otherwise.
    """
    try:
        import pyautogui
    except ImportError as exc:
        logger.warning("pyautogui not installed; auto-click Add disabled (%s)", exc)
        return False

    pyautogui.FAILSAFE = False

    if initial_delay > 0:
        time.sleep(initial_delay)

    start = time.time()
    last_pos: Optional[Tuple[int, int]] = None
    stable_hits = 0

    while time.time() - start < timeout_seconds:
        # Re-check the foreground window every poll: if the user has
        # switched tabs/windows away from the Reddit submit page, the
        # screen we're scanning is no longer the page we opened, and any
        # blue-button blob we might find belongs to something unrelated.
        title = _foreground_window_title()
        if not _is_reddit_foreground(title):
            logger.debug(
                "Foreground window is %r; skipping this poll until Reddit is in front",
                title,
            )
            stable_hits = 0
            last_pos = None
            time.sleep(poll_interval)
            continue

        pos = _find_add_button()
        if pos is not None:
            if last_pos is not None and abs(pos[0] - last_pos[0]) < 8 and abs(pos[1] - last_pos[1]) < 8:
                stable_hits += 1
            else:
                stable_hits = 1
            last_pos = pos
            if stable_hits >= 2:
                cx, cy = pos
                try:
                    logger.info("Clicking Reddit flair Add button at (%d, %d)", cx, cy)
                    pyautogui.click(cx, cy)
                    return True
                except Exception as exc:
                    logger.warning("pyautogui.click failed: %s", exc)
                    return False
        else:
            stable_hits = 0
            last_pos = None
        time.sleep(poll_interval)

    # Timed out — take one more snapshot and save a debug image so we can
    # see what the screen actually looked like when no button was found.
    logger.info(
        "Reddit flair Add button not located within %.1fs; leaving the dialog for manual click",
        timeout_seconds,
    )
    _find_add_button(save_debug_on_miss=True)
    return False
