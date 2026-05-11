"""study-bot: integrated scheduler + poster.

Single long-running process that:

1. Builds a chronological queue of every Reddit post in ``reddit_posts.yml``
   for the current week, applying a uniform random ±jitter_seconds offset
   to each fire time so the actual post moment is unpredictable.
2. Waits until each slot, runs the appropriate count check (Google Sheets
   for SURVEYS, MySQL via SSH tunnel for EXPERIMENTS), opens the Reddit
   submit tab, then sends ONE combined Telegram message per slot that
   includes both the count result and whether the post went up.
3. After processing all slots in the current week, the loop rebuilds the
   queue (slots automatically roll forward to the next week) and keeps
   running.

Telegram is the alarm channel:
  - Startup heartbeat (queue length, first slot).
  - Per-slot combined message: count + post result (e.g. "Posted: r/X OK").
  - "[study-bot ERROR] ..." on API/DB/posting failure.
  - "[study-bot CRASH] ..." on unhandled exception.
  - Stop confirmation when "stop" is received over Telegram.

CLI
---
    study-bot run                       (production loop, runs forever)
    study-bot run --now                 (fire all queued slots immediately, exit)
    study-bot run --dry-run             (open tabs but never auto-click Add/Post/Submit;
                                          useful for verifying prefill content without spamming)
    study-bot run --only survey         (filter queue to surveys only)
    study-bot run --only experiment     (filter queue to experiments only)

Flags combine: e.g. ``study-bot run --now --dry-run --only survey``.

Remote stop
-----------
Send "stop" (case-insensitive, any surrounding whitespace) to the bot's
Telegram chat and the process exits cleanly after the current slot
completes.  Task Scheduler's restart-on-failure rule does NOT fire on a
clean exit (code 0), so the bot stays stopped until you restart it
manually or reboot.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import EXPERIMENTS, REDDIT_POSTS, REDDIT_SETTINGS, SURVEYS
from .database import get_experiment_counts
from .logger import get_logger
from .notify import send_telegram, start_stop_listener
from .reddit_post import PostResult, prefill as reddit_prefill
from .scheduler import apply_jitter, next_occurrence, wait_until
from .sheets import get_response_count

_STATE_FILE = Path(__file__).resolve().parents[2] / "logs" / "last_counts.json"

# Default schedule for posts (or surveys/experiments without a Reddit post)
# that omit a `schedule:` block entirely.
_DEFAULT_SCHEDULE = {
    "weekday": 3,   # Thursday
    "hour": 15,
    "minute": 50,
    "tz": "America/New_York",
}

_WEEKDAY_NAMES = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


# ---------------------------------------------------------------------------
# State (last counts)
# ---------------------------------------------------------------------------


def _load_last_counts() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_last_counts(counts: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(counts, indent=2), encoding="utf-8")


def _delta_suffix(count: int, last_count: int | None) -> str:
    if last_count is None:
        return ""
    delta = count - last_count
    if delta > 0:
        return f" (increased by {delta})"
    if delta < 0:
        return f" (decreased by {abs(delta)})"
    return " (no change)"


def _build_count_line(label: str, count: int, last_count: int | None) -> str:
    return f"{label} # of responses = {count}{_delta_suffix(count, last_count)}"


def _build_experiment_count_lines(
    label: str,
    total: int,
    complete: int,
    last_total: int | None,
    last_complete: int | None,
) -> str:
    total_line = f"{label} total rows = {total}{_delta_suffix(total, last_total)}"
    complete_line = f"Completed = {complete}{_delta_suffix(complete, last_complete)}"
    return f"{total_line}\n{complete_line}"


# ---------------------------------------------------------------------------
# Schedule expansion
# ---------------------------------------------------------------------------


def _parse_weekday(name) -> int:
    if isinstance(name, int):
        return name
    if isinstance(name, str):
        key = name.strip().lower()
        if key in _WEEKDAY_NAMES:
            return _WEEKDAY_NAMES[key]
    raise ValueError(f"Unrecognised weekday: {name!r}")


def _slot_to_dt(slot: dict) -> datetime:
    weekday = _parse_weekday(slot.get("weekday"))
    hour = int(slot.get("hour"))
    minute = int(slot.get("minute"))
    tz = slot.get("tz", _DEFAULT_SCHEDULE["tz"])
    return next_occurrence(weekday, hour, minute, tz)


def _post_target_dts(post: dict) -> list[datetime]:
    sched = post.get("schedule") if isinstance(post, dict) else None
    if sched is None:
        return [_slot_to_dt(_DEFAULT_SCHEDULE)]
    if isinstance(sched, dict):
        return [_slot_to_dt(sched)]
    if isinstance(sched, list):
        return [_slot_to_dt(s) for s in sched if isinstance(s, dict)]
    raise ValueError(f"Unrecognised schedule shape: {sched!r}")


def _expand_post_schedules(label: str):
    cfg = REDDIT_POSTS.get(label)
    posts = cfg if isinstance(cfg, list) else ([cfg] if cfg else [])
    for post in posts:
        if not isinstance(post, dict):
            continue
        for target in _post_target_dts(post):
            yield post, target


def _build_unified_queue(jitter_seconds: int) -> list[tuple]:
    """Build a chronological queue of (kind, source, post, target_jittered)."""
    items = []

    for survey in SURVEYS:
        any_posts = False
        for post, target in _expand_post_schedules(survey["label"]):
            items.append(("survey", survey, post, apply_jitter(target, jitter_seconds)))
            any_posts = True
        if not any_posts:
            target = _slot_to_dt(_DEFAULT_SCHEDULE)
            items.append(("survey", survey, None, apply_jitter(target, jitter_seconds)))

    for experiment in EXPERIMENTS:
        any_posts = False
        for post, target in _expand_post_schedules(experiment["label"]):
            items.append(("experiment", experiment, post, apply_jitter(target, jitter_seconds)))
            any_posts = True
        if not any_posts:
            target = _slot_to_dt(_DEFAULT_SCHEDULE)
            items.append(("experiment", experiment, None, apply_jitter(target, jitter_seconds)))

    items.sort(key=lambda t: t[3])
    return items


# ---------------------------------------------------------------------------
# Per-slot actions (fetch only — no Telegram here)
# ---------------------------------------------------------------------------


def _fetch_survey(
    survey: dict,
    last_counts: dict,
    updated_counts: dict,
    log,
) -> tuple[bool, str]:
    """Fetch survey count.  Returns (success, count_line_text)."""
    label = survey["label"]
    try:
        count = get_response_count(survey["sheet_id"], survey["range"])
        line = _build_count_line(label, count, last_counts.get(label))
        updated_counts[label] = count
        log.info(line)
        return True, line
    except Exception as exc:
        log.error("Error fetching survey %r\n%s", label, traceback.format_exc())
        return False, str(exc)


def _fetch_experiment(
    experiment: dict,
    last_counts: dict,
    updated_counts: dict,
    log,
) -> tuple[bool, str]:
    """Fetch experiment counts.  Returns (success, count_lines_text)."""
    label = experiment["label"]
    table = experiment["table"]
    total_key = f"{label} total"
    complete_key = f"{label} complete"
    try:
        counts = get_experiment_counts(experiment["database"], table)
        total = counts["total"]
        complete = counts["complete"]
        lines = _build_experiment_count_lines(
            label, total, complete,
            last_counts.get(total_key),
            last_counts.get(complete_key),
        )
        updated_counts[total_key] = total
        updated_counts[complete_key] = complete
        log.info("%s (%s): total=%d complete=%d", label, table, total, complete)
        return True, lines
    except Exception as exc:
        log.error("Error fetching experiment %r\n%s", label, traceback.format_exc())
        return False, str(exc)


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


def _format_target(dt: datetime) -> str:
    return dt.strftime("%a %Y-%m-%d %H:%M %Z")


def _telegram_safe(message: str, log) -> None:
    try:
        send_telegram(message)
    except Exception:
        log.error("Failed to send Telegram message:\n%s", traceback.format_exc())


def _process_queue(
    queue: list[tuple],
    args: argparse.Namespace,
    last_counts: dict,
    updated_counts: dict,
    log,
    stop_event: threading.Event,
) -> None:
    n = len(queue)
    if n == 0:
        log.info("Empty queue (no surveys/experiments to process).")
        return

    log.info(
        "Queue: %d slots, first=%s, last=%s",
        n, _format_target(queue[0][3]), _format_target(queue[-1][3]),
    )

    for i, (kind, source, post, target) in enumerate(queue, 1):
        if stop_event.is_set():
            log.info("Stop requested; aborting queue at slot %d/%d", i, n)
            return

        log.info(
            "[%d/%d] %s '%s' -> r/%s scheduled for %s%s",
            i, n, kind, source["label"],
            (post["subreddit"] if post else "<no post>"),
            _format_target(target),
            " [DRY-RUN]" if args.dry_run else "",
        )

        if not args.now:
            reached = wait_until(target, log, stop_event=stop_event)
            if not reached:
                log.info("Stop requested during wait; exiting queue")
                return

        # --- Fetch counts ---
        if kind == "survey":
            fetch_ok, count_text = _fetch_survey(source, last_counts, updated_counts, log)
        else:
            fetch_ok, count_text = _fetch_experiment(source, last_counts, updated_counts, log)

        if not fetch_ok:
            # Send error and skip the post for this slot.
            label = source["label"]
            _telegram_safe(f"[study-bot ERROR] {label}: {count_text}", log)
            _save_last_counts(updated_counts)
            continue

        # --- Open Reddit tab + click flow ---
        if post is not None:
            subreddit = post["subreddit"]
            try:
                result: PostResult = reddit_prefill(
                    subreddit=subreddit,
                    title=post["title"],
                    post_type=post.get("post_type", "link"),
                    link_url=post.get("link_url", ""),
                    body=post.get("body", ""),
                    flair=post.get("flair", ""),
                    auto_click_add=(False if args.dry_run else bool(post.get("auto_click_add", False))),
                    auto_post=(False if args.dry_run else bool(post.get("auto_post", False))),
                    dry_run=args.dry_run,
                    stop_event=stop_event,
                )
            except Exception as exc:
                result = PostResult(
                    tab_opened=False,
                    reason=f"prefill() raised: {exc}",
                    dry_run=args.dry_run,
                )
                log.warning("reddit_prefill error: %s\n%s", exc, traceback.format_exc())

            post_line = f"Posted: r/{subreddit} {result.summary_line()}"
            log.info(post_line)

            # One combined Telegram message: count + post result.
            prefix = "[DRY-RUN] " if args.dry_run else ""
            combined = f"{prefix}{count_text}\n{post_line}"

            if not result.success and not args.dry_run:
                _telegram_safe(f"[study-bot ERROR] {combined}", log)
            else:
                _telegram_safe(combined, log)
        else:
            # No Reddit post configured — send count only.
            prefix = "[DRY-RUN] " if args.dry_run else ""
            _telegram_safe(f"{prefix}{count_text}", log)

        _save_last_counts(updated_counts)


def _run_forever(args: argparse.Namespace, log, stop_event: threading.Event) -> None:
    last_counts = _load_last_counts()
    updated_counts = dict(last_counts)
    jitter = int(REDDIT_SETTINGS.get("jitter_seconds", 120))

    sent_startup = False

    while True:
        if stop_event.is_set():
            log.info("Stop requested at loop start; exiting")
            return

        queue = _build_unified_queue(jitter)
        if args.only:
            queue = [q for q in queue if q[0] == args.only]

        if not sent_startup:
            first = queue[0][3] if queue else None
            heartbeat = (
                f"[study-bot] started ({len(queue)} slots, jitter ±{jitter}s"
                + (f", --only={args.only}" if args.only else "")
                + (", --dry-run" if args.dry_run else "")
                + ")"
                + (f"\nFirst slot: {_format_target(first)}" if first else "")
                + "\nSend 'stop' here to halt the bot"
            )
            log.info(heartbeat.replace("\n", " | "))
            _telegram_safe(heartbeat, log)
            sent_startup = True

        _process_queue(queue, args, last_counts, updated_counts, log, stop_event)

        if stop_event.is_set():
            log.info("Stop requested; exiting after queue")
            return

        if args.now:
            log.info("--now: queue processed, exiting")
            return

        log.info("All slots for this cycle complete; rebuilding queue")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="study-bot",
        description=(
            "Integrated scheduler + poster. Walks a weekly per-subreddit "
            "slot table forever (rebuilt each cycle), checks survey/experiment "
            "counts at each slot, sends Telegram updates, and opens Reddit "
            "submit tabs that the companion userscript + OS-click driver "
            "complete."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Start the scheduler.")
    run_p.add_argument(
        "--now", action="store_true",
        help="Fire every slot immediately (no waits) and exit. Useful for testing.",
    )
    run_p.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Open Reddit submit tabs to verify prefill content, but never "
            "auto-click Add/Post/Submit-without-editing.  Telegram messages "
            "are still sent (with [DRY-RUN] prefix)."
        ),
    )
    run_p.add_argument(
        "--only", choices=["survey", "experiment"], default=None,
        help="Filter the queue to one kind only.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> None:
    args = _parse_args(argv)
    log = get_logger()
    log.info(
        "study-bot %s (now=%s, dry_run=%s, only=%s)",
        args.command, args.now, args.dry_run, args.only,
    )

    stop_event = threading.Event()
    start_stop_listener(stop_event, log)

    try:
        _run_forever(args, log, stop_event)
    except KeyboardInterrupt:
        log.info("study-bot interrupted by user (Ctrl+C)")
    except Exception as exc:
        log.error("study-bot crashed:\n%s", traceback.format_exc())
        _telegram_safe(f"[study-bot CRASH] {exc}", log)
        sys.exit(1)

    log.info("study-bot exit")


if __name__ == "__main__":
    main()
