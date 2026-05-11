"""OS-level mouse clicks driven by the page itself (title-IPC).

Background
----------
Previous versions of this module tried to locate buttons on-screen by
scanning the screenshot for blue pixel blobs.  That approach was fragile:
the Reddit composer and Chrome window can be positioned anywhere on a
multi-monitor ultrawide setup, sidebar avatars have the same blue colour
and similar shape as the buttons, and zoom / DPR changes shift geometry.

The new approach
----------------
The companion Tampermonkey userscript (v0.8+,
`userscripts/reddit-auto-flair.user.js`) has full DOM access.  It finds
each target element by role / text / aria-label, calls scrollIntoView() so
the element is definitely visible, and then encodes its exact physical-pixel
screen coordinates in document.title:

    [SBP:<phase>:<x>,<y>] <original title>

Windows exposes the active tab's document.title in the foreground window
title, so Python can read it with pyautogui.getActiveWindow().title.  Once
the marker is seen, Python fires one OS-level pyautogui.click at the given
(x, y).  Because the click comes from the OS and not from JavaScript, Reddit
sees isTrusted=true and accepts it.

Phase sequence
--------------
    add     → flair dialog "Add" button ready
    post    → composer "Post" button ready
    submit  → warning-dialog "Submit without editing" button ready (optional)
    done    → submission complete, no further action needed
    none    → userscript could not locate a button (treated as timeout here)

The 250 ms setInterval in the userscript keeps the marker in the title even
when Reddit's SPA rewrites document.title.

Foreground robustness
---------------------
`wait_for_phase` calls `bring_reddit_to_foreground` on every polling
iteration where Reddit is not in front.  This ensures that a stray
notification, cmd.exe window, or screensaver that steals focus cannot
prevent the click flow from completing — the next 0.4 s poll yanks Chrome
back to front via the Win32 SetForegroundWindow API.

Safety
------
* Every click is guarded by a final `_is_reddit_foreground` check.
* On timeout the function returns False/None and logs a human-readable
  message; the user can finish manually.  No exceptions are raised.
* All functions accept an optional `stop_event: threading.Event`; when set
  the poll loop exits early and returns None so the caller can clean up.
"""

from __future__ import annotations

import ctypes
import logging
import re
import threading
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Regex to parse [SBP:<phase>:<x>,<y>] or [SBP:<phase>] from a window title.
_PHASE_MARKER_RE = re.compile(r"\[SBP:(\w+)(?::(-?\d+),(-?\d+))?\]")

# Substring matches for the foreground window's title so we only click when
# a Reddit submit page is in the foreground.
_FOREGROUND_TITLE_NEEDLES = ("reddit", "r/")

# Win32 constants for keybd_event / ShowWindow.
_VK_MENU = 0x12          # Alt key
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_SW_RESTORE = 9


def _foreground_window_title() -> Optional[str]:
    """Return the foreground window's title, or None if unavailable."""
    try:
        import pyautogui
        win = pyautogui.getActiveWindow()
        if win is None:
            return None
        return str(getattr(win, "title", "") or "")
    except Exception as exc:
        logger.debug("Could not read foreground window title: %s", exc)
        return None


def _is_reddit_foreground(title: Optional[str]) -> bool:
    if not title:
        return False
    lower = title.lower()
    return any(needle in lower for needle in _FOREGROUND_TITLE_NEEDLES)


def bring_reddit_to_foreground() -> bool:
    """Find a Chrome window showing a Reddit submit page and raise it.

    Uses pyautogui.getAllWindows() to enumerate visible windows, picks the
    one whose title contains 'reddit' or 'r/' (preferring a window that
    also contains '[SBP:' — the userscript-controlled submit tab), then
    uses the Win32 'press Alt before SetForegroundWindow' trick to bypass
    Windows' foreground-lock restriction.

    Returns True if a Reddit window is foreground after the attempt,
    False if no such window was found or the activation failed.
    """
    try:
        import pyautogui
        all_wins = pyautogui.getAllWindows()
    except Exception as exc:
        logger.debug("bring_reddit_to_foreground: getAllWindows failed: %s", exc)
        return False

    candidates = [w for w in all_wins if _is_reddit_foreground(getattr(w, "title", None))]
    if not candidates:
        logger.debug("bring_reddit_to_foreground: no Reddit window found")
        return False

    # Prefer the submit tab that already has the SBP marker.
    sbp_wins = [w for w in candidates if "[SBP:" in (getattr(w, "title", None) or "")]
    target = sbp_wins[0] if sbp_wins else candidates[0]

    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]

        # Press and immediately release the Alt key.  This tricks Windows
        # into allowing the subsequent SetForegroundWindow call to succeed
        # even when another process currently owns the foreground lock.
        user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_EXTENDEDKEY, 0)
        user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_EXTENDEDKEY | _KEYEVENTF_KEYUP, 0)

        # Try pyautogui's built-in activate() first (works on most systems).
        try:
            target.activate()
        except Exception:
            pass

        # Fall back to raw Win32 if activate() didn't do the job.
        hwnd = getattr(target, "_hWnd", None) or getattr(target, "hWnd", None)
        if hwnd:
            user32.ShowWindow(hwnd, _SW_RESTORE)
            user32.SetForegroundWindow(hwnd)

        time.sleep(0.15)  # give Windows a moment to process the focus change

    except Exception as exc:
        logger.debug("bring_reddit_to_foreground: Win32 activation failed: %s", exc)

    result = _is_reddit_foreground(_foreground_window_title())
    if result:
        logger.debug("bring_reddit_to_foreground: Reddit is now foreground")
    else:
        logger.debug("bring_reddit_to_foreground: Reddit still not foreground after attempt")
    return result


def _parse_phase_marker(title: str) -> Optional[Tuple[str, Optional[int], Optional[int]]]:
    """Parse the [SBP:phase:x,y] marker from a window title.

    Returns (phase, x, y) where x/y are ints or None (for done/none phases).
    Returns None if no marker is present.
    """
    if not title:
        return None
    m = _PHASE_MARKER_RE.search(title)
    if not m:
        return None
    phase = m.group(1)
    x = int(m.group(2)) if m.group(2) is not None else None
    y = int(m.group(3)) if m.group(3) is not None else None
    return phase, x, y


def wait_for_phase(
    phase: str,
    timeout_seconds: float,
    poll_interval: float = 0.4,
    stop_event: Optional[threading.Event] = None,
) -> Optional[Tuple[int, int]]:
    """Poll the foreground window title until [SBP:<phase>:x,y] appears.

    Returns (x, y) physical-pixel screen coordinates on success, or None on
    timeout.  For terminal phases (done, none) where no coordinates are
    embedded, returns the sentinel (0, 0) so callers can distinguish them
    from timeout (None).

    On each poll where the Reddit tab is not foreground,
    `bring_reddit_to_foreground()` is called to try to raise it.  A
    non-Reddit foreground window no longer pauses the timeout countdown.

    If `stop_event` is given and becomes set, returns None immediately.
    """
    start = time.time()
    foreground_failures = 0

    while time.time() - start < timeout_seconds:
        if stop_event is not None and stop_event.is_set():
            logger.debug("wait_for_phase(%r): stop_event set; aborting", phase)
            return None

        title = _foreground_window_title()
        if not _is_reddit_foreground(title):
            foreground_failures += 1
            # Try to bring Reddit to front on every poll.
            brought = bring_reddit_to_foreground()
            if brought:
                foreground_failures = 0
                logger.debug("Brought Reddit to foreground for %s phase", phase)
            else:
                if foreground_failures == 1 or foreground_failures % 10 == 0:
                    logger.info(
                        "Foreground window is %r; attempting to bring Reddit forward "
                        "(phase=%s, attempts=%d)",
                        title, phase, foreground_failures,
                    )
            time.sleep(poll_interval)
            continue

        foreground_failures = 0
        result = _parse_phase_marker(title or "")
        if result is not None:
            found_phase, x, y = result
            if found_phase == phase:
                if x is not None and y is not None:
                    logger.debug("Detected [SBP:%s] at (%d, %d)", phase, x, y)
                    return x, y
                # Terminal phase with no coordinates (done / none).
                logger.debug("Detected [SBP:%s] (no coordinates)", phase)
                return 0, 0
            elif found_phase in ("done", "none"):
                # The userscript jumped past the expected phase (e.g. no
                # warning dialog appeared, so it went straight to done).
                logger.debug(
                    "Detected [SBP:%s] while waiting for phase %r; stopping",
                    found_phase, phase,
                )
                return None

        time.sleep(poll_interval)

    logger.info(
        "Phase %r not signaled by userscript within %.0fs; leaving for manual action",
        phase, timeout_seconds,
    )
    return None


def _click(x: int, y: int, label: str) -> bool:
    """Issue a single OS-level mouse click at (x, y)."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        logger.info("OS-clicking Reddit %s at (%d, %d)", label, x, y)
        pyautogui.click(x, y)
        return True
    except Exception as exc:
        logger.warning("pyautogui.click(%s) failed: %s", label, exc)
        return False


def click_add_button(
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.4,
    initial_delay: float = 3.0,
    stop_event: Optional[threading.Event] = None,
) -> bool:
    """Wait for [SBP:add] from the userscript, then OS-click the flair Add button.

    Returns True if the click was issued, False on timeout.
    """
    if initial_delay > 0:
        time.sleep(initial_delay)
    pos = wait_for_phase("add", timeout_seconds, poll_interval, stop_event=stop_event)
    if pos is None:
        logger.info(
            "Flair Add button not signaled within %.0fs; leaving dialog for manual click",
            timeout_seconds,
        )
        return False
    return _click(pos[0], pos[1], "flair Add")


def click_post_button(
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.4,
    initial_delay: float = 1.5,
    stop_event: Optional[threading.Event] = None,
) -> bool:
    """Wait for [SBP:post] from the userscript, then OS-click the Post button.

    Should be called after click_add_button() has returned.  The userscript
    detects that the flair dialog closed and then emits [SBP:post].

    Returns True if a click was issued, False on timeout.
    """
    if initial_delay > 0:
        time.sleep(initial_delay)
    pos = wait_for_phase("post", timeout_seconds, poll_interval, stop_event=stop_event)
    if pos is None:
        logger.info(
            "Post button not signaled within %.0fs; leaving composer for manual click",
            timeout_seconds,
        )
        return False
    return _click(pos[0], pos[1], "Post")


def click_submit_without_editing(
    timeout_seconds: float = 25.0,
    poll_interval: float = 0.4,
    initial_delay: float = 1.5,
    stop_event: Optional[threading.Event] = None,
) -> bool:
    """Wait for [SBP:submit] or [SBP:done], then act accordingly.

    The warning dialog ("Your post may break these rules") only appears
    sometimes.  If the userscript emits [SBP:done] before [SBP:submit], the
    post was submitted directly and we return False (no click needed).  If
    [SBP:submit] appears, we OS-click and return True.

    Returns True if "Submit without editing" was clicked, False otherwise.
    """
    if initial_delay > 0:
        time.sleep(initial_delay)
    pos = wait_for_phase("submit", timeout_seconds, poll_interval, stop_event=stop_event)
    if pos is None:
        logger.info(
            "No warning dialog signaled within %.0fs; assuming post submitted directly",
            timeout_seconds,
        )
        return False
    return _click(pos[0], pos[1], "Submit without editing")
