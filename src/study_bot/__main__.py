import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import EXPERIMENTS, REDDIT_POSTS, SURVEYS
from .database import get_experiment_counts
from .logger import get_logger
from .notify import send_telegram
from .reddit_post import prefill as reddit_prefill
from .scheduler import THURSDAY, next_occurrence, wait_until
from .sheets import get_response_count

_STATE_FILE = Path(__file__).resolve().parents[2] / "logs" / "last_counts.json"

# Default schedule: a post entry without an explicit `schedule:` block
# falls back to this slot.  Eventually every post should carry its own
# schedule and this default will only matter for surveys that have no
# Reddit posts configured (so the check still runs at a known time).
_DEFAULT_SCHEDULE_WEEKDAY = THURSDAY
_DEFAULT_SCHEDULE_HOUR = 15
_DEFAULT_SCHEDULE_MINUTE = 50
_DEFAULT_SCHEDULE_TZ = "America/New_York"

_WEEKDAY_NAMES = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


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


def _build_message(label: str, count: int, last_count: int | None) -> str:
    return f"{label} # of responses = {count}{_delta_suffix(count, last_count)}"


def _build_experiment_message(
    label: str,
    table: str,
    total: int,
    complete: int,
    last_total: int | None,
    last_complete: int | None,
) -> str:
    total_line = f"{label} total rows = {total}{_delta_suffix(total, last_total)}"
    complete_line = f"Completed = {complete}{_delta_suffix(complete, last_complete)}"
    return f"{total_line}\n{complete_line}"


def _parse_weekday(name) -> int:
    if isinstance(name, int):
        return name
    if isinstance(name, str):
        key = name.strip().lower()
        if key in _WEEKDAY_NAMES:
            return _WEEKDAY_NAMES[key]
    raise ValueError(f"Unrecognised weekday: {name!r}")


def _post_target_dt(post: dict) -> datetime:
    """Return the next scheduled run time for a single post entry.

    Reads `post["schedule"]` if present:

        schedule:
          weekday: thursday
          hour: 16
          minute: 5
          tz: America/New_York   # optional

    Falls back to the global default schedule when no `schedule` block
    is present.
    """
    sched = post.get("schedule") if isinstance(post, dict) else None
    if sched:
        weekday = _parse_weekday(sched.get("weekday"))
        hour = int(sched.get("hour"))
        minute = int(sched.get("minute"))
        tz = sched.get("tz", _DEFAULT_SCHEDULE_TZ)
        return next_occurrence(weekday, hour, minute, tz)
    return next_occurrence(
        _DEFAULT_SCHEDULE_WEEKDAY,
        _DEFAULT_SCHEDULE_HOUR,
        _DEFAULT_SCHEDULE_MINUTE,
        _DEFAULT_SCHEDULE_TZ,
    )


def _wait_for_post(target: datetime, log, args) -> bool:
    """Wait until `target` unless --now was passed.  Returns False if cancelled."""
    if args.now:
        return True
    try:
        wait_until(target, log)
        return True
    except KeyboardInterrupt:
        log.info("Schedule wait cancelled by user; skipping remaining posts")
        return False


def _open_one_post(label: str, post: dict, log) -> None:
    try:
        reddit_prefill(
            subreddit=post["subreddit"],
            title=post["title"],
            post_type=post.get("post_type", "link"),
            link_url=post.get("link_url", ""),
            body=post.get("body", ""),
            flair=post.get("flair", ""),
            auto_click_add=bool(post.get("auto_click_add", False)),
            auto_post=bool(post.get("auto_post", False)),
        )
    except Exception:
        log.warning(
            "Reddit pre-fill skipped for '%s' -> r/%s\n%s",
            label, post.get("subreddit", "?"), traceback.format_exc(),
        )


def _check_one_survey(survey: dict, last_counts: dict, updated_counts: dict, log) -> None:
    label = survey["label"]
    sheet_id = survey["sheet_id"]
    range_a1 = survey["range"]
    try:
        count = get_response_count(sheet_id, range_a1)
        message = _build_message(label, count, last_counts.get(label))
        updated_counts[label] = count
        log.info(message)
        try:
            send_telegram(message)
        except Exception:
            log.error(
                "Failed to send Telegram success notification\n%s",
                traceback.format_exc(),
            )
    except Exception as exc:
        log.error("Error processing survey '%s'\n%s", label, traceback.format_exc())
        try:
            send_telegram(f"[study-bot ERROR] {label}: {exc}")
        except Exception:
            log.error(
                "Failed to send Telegram error notification\n%s",
                traceback.format_exc(),
            )


def _check_one_experiment(experiment: dict, last_counts: dict, updated_counts: dict, log) -> None:
    label = experiment["label"]
    database = experiment["database"]
    table = experiment["table"]
    total_key = f"{label} total"
    complete_key = f"{label} complete"
    try:
        counts = get_experiment_counts(database, table)
        total = counts["total"]
        complete = counts["complete"]
        message = _build_experiment_message(
            label, table, total, complete,
            last_counts.get(total_key),
            last_counts.get(complete_key),
        )
        updated_counts[total_key] = total
        updated_counts[complete_key] = complete
        log.info("%s (%s): total=%d complete=%d", label, table, total, complete)
        try:
            send_telegram(message)
        except Exception:
            log.error(
                "Failed to send Telegram notification for '%s'\n%s",
                label, traceback.format_exc(),
            )
    except Exception as exc:
        log.error(
            "Error processing experiment '%s'\n%s",
            label, traceback.format_exc(),
        )
        try:
            send_telegram(f"[study-bot ERROR] {label}: {exc}")
        except Exception:
            log.error(
                "Failed to send Telegram error notification\n%s",
                traceback.format_exc(),
            )


def _build_post_items(items_source: list, label_key: str) -> list:
    """Build a chronological list of (target_dt, source, post) tuples.

    `items_source` is either SURVEYS or EXPERIMENTS.  For each entry,
    looks up its Reddit posts in REDDIT_POSTS by label.  When an entry
    has no Reddit posts, emits a single (target, entry, None) tuple at
    the global default time so the check still runs at a known slot.
    """
    out = []
    for source in items_source:
        label = source[label_key]
        cfg = REDDIT_POSTS.get(label)
        post_list = cfg if isinstance(cfg, list) else ([cfg] if cfg else [])
        if not post_list:
            target = next_occurrence(
                _DEFAULT_SCHEDULE_WEEKDAY,
                _DEFAULT_SCHEDULE_HOUR,
                _DEFAULT_SCHEDULE_MINUTE,
                _DEFAULT_SCHEDULE_TZ,
            )
            out.append((target, source, None))
        else:
            for post in post_list:
                out.append((_post_target_dt(post), source, post))
    out.sort(key=lambda triple: triple[0])
    return out


def _run_surveys(log, last_counts: dict, updated_counts: dict, args) -> None:
    items = _build_post_items(SURVEYS, "label")
    surveys_checked = set()
    for target, survey, post in items:
        if not _wait_for_post(target, log, args):
            return
        label = survey["label"]
        if label not in surveys_checked:
            _check_one_survey(survey, last_counts, updated_counts, log)
            surveys_checked.add(label)
        if post is not None:
            _open_one_post(label, post, log)


def _run_experiments(log, last_counts: dict, updated_counts: dict, args) -> None:
    items = _build_post_items(EXPERIMENTS, "label")
    experiments_checked = set()
    for target, experiment, post in items:
        if not _wait_for_post(target, log, args):
            return
        label = experiment["label"]
        if label not in experiments_checked:
            _check_one_experiment(experiment, last_counts, updated_counts, log)
            experiments_checked.add(label)
        if post is not None:
            _open_one_post(label, post, log)


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="study-bot",
        description=(
            "Check survey / experiment counts, send a Telegram update, and "
            "open Reddit submit tabs pre-filled with the configured posts. "
            "By default, each post waits for its own scheduled slot before "
            "running."
        ),
    )
    parser.add_argument(
        "mode",
        choices=["survey", "experiment"],
        help=(
            "survey: run Google Sheets surveys + their Reddit posts. "
            "experiment: run MySQL experiments + their Reddit posts."
        ),
    )
    parser.add_argument(
        "--now",
        action="store_true",
        help=(
            "Skip every scheduled wait and run all posts back-to-back "
            "immediately (useful for testing)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> None:
    args = _parse_args(argv)
    log = get_logger()
    log.info("study-bot run started (mode=%s, now=%s)", args.mode, args.now)

    last_counts = _load_last_counts()
    updated_counts = dict(last_counts)

    if args.mode == "survey":
        _run_surveys(log, last_counts, updated_counts, args)
    elif args.mode == "experiment":
        _run_experiments(log, last_counts, updated_counts, args)
    else:
        log.error("Unknown mode: %s", args.mode)
        sys.exit(2)

    _save_last_counts(updated_counts)
    log.info("study-bot run finished")


if __name__ == "__main__":
    main()
