import argparse
import json
import sys
import traceback
from pathlib import Path

from .config import EXPERIMENTS, REDDIT_POSTS, SURVEYS
from .database import get_experiment_counts
from .logger import get_logger
from .notify import send_telegram
from .reddit_post import prefill as reddit_prefill
from .sheets import get_response_count

_STATE_FILE = Path(__file__).resolve().parents[2] / "logs" / "last_counts.json"


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


def _open_reddit_posts_for(label: str, log) -> None:
    """Open every configured Reddit submit tab for the given label.

    Schema accepts either a single dict (legacy) or a list of dicts so a
    single survey/experiment can post to multiple subreddits.  Failures
    in one entry never stop the next one.
    """
    post_cfg = REDDIT_POSTS.get(label)
    if not post_cfg:
        return
    post_list = post_cfg if isinstance(post_cfg, list) else [post_cfg]
    for one_post in post_list:
        try:
            reddit_prefill(
                subreddit=one_post["subreddit"],
                title=one_post["title"],
                post_type=one_post.get("post_type", "link"),
                link_url=one_post.get("link_url", ""),
                body=one_post.get("body", ""),
                flair=one_post.get("flair", ""),
                auto_click_add=bool(one_post.get("auto_click_add", False)),
            )
        except Exception:
            log.warning(
                "Reddit pre-fill skipped for '%s' -> r/%s\n%s",
                label, one_post.get("subreddit", "?"), traceback.format_exc(),
            )


def _run_surveys(log, last_counts: dict, updated_counts: dict) -> None:
    for survey in SURVEYS:
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
                log.error("Failed to send Telegram success notification\n%s", traceback.format_exc())

            _open_reddit_posts_for(label, log)

        except Exception as e:
            log.error("Error processing survey '%s'\n%s", label, traceback.format_exc())
            error_message = f"[study-bot ERROR] {label}: {e}"
            try:
                send_telegram(error_message)
            except Exception:
                log.error("Failed to send Telegram error notification\n%s", traceback.format_exc())


def _run_experiments(log, last_counts: dict, updated_counts: dict) -> None:
    for experiment in EXPERIMENTS:
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
                log.error("Failed to send Telegram notification for '%s'\n%s", label, traceback.format_exc())

            _open_reddit_posts_for(label, log)

        except Exception as e:
            log.error("Error processing experiment '%s'\n%s", label, traceback.format_exc())
            error_message = f"[study-bot ERROR] {label}: {e}"
            try:
                send_telegram(error_message)
            except Exception:
                log.error("Failed to send Telegram error notification\n%s", traceback.format_exc())


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="study-bot",
        description=(
            "Check survey / experiment counts, send a Telegram update, and "
            "open Reddit submit tabs pre-filled with the configured posts."
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    log = get_logger()
    log.info("study-bot run started (mode=%s)", args.mode)

    last_counts = _load_last_counts()
    updated_counts = dict(last_counts)

    if args.mode == "survey":
        _run_surveys(log, last_counts, updated_counts)
    elif args.mode == "experiment":
        _run_experiments(log, last_counts, updated_counts)
    else:
        log.error("Unknown mode: %s", args.mode)
        sys.exit(2)

    _save_last_counts(updated_counts)
    log.info("study-bot run finished")


if __name__ == "__main__":
    main()
