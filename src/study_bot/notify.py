"""Telegram helpers: send messages and listen for remote-stop commands.

`send_telegram(text)` — send a message to the configured chat.

`start_stop_listener(stop_event, log)` — spawn a daemon thread that
long-polls the Telegram Bot API `getUpdates` endpoint and sets
`stop_event` when a message whose stripped text is exactly "stop"
(case-insensitive) arrives from the configured `TELEGRAM_CHAT_ID`.

Drain-on-start: the listener calls `getUpdates` once without an offset
on startup to find the current max update_id, then begins polling from
max_id+1.  This ensures that a "stop" message sent while the bot was
offline is ignored — only messages sent after startup are obeyed.

Chat-ID gating: only messages whose `chat.id` matches `TELEGRAM_CHAT_ID`
(converted to int for comparison) are acted upon, so a third party who
somehow learns the bot token cannot stop the process.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import requests

from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
_AUTHORIZED_CHAT_ID = int(TELEGRAM_CHAT_ID)


def _redact(text) -> str:
    """Replace the bot token in `text` so it is never written to the log."""
    return str(text).replace(TELEGRAM_BOT_TOKEN, "<token>")


def send_telegram(text: str) -> None:
    url = f"{_API}/sendMessage"
    response = requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
        timeout=15,
    )
    response.raise_for_status()


def _get_updates(offset: Optional[int], timeout_secs: int = 25) -> list[dict]:
    """Call getUpdates and return the list of Update objects (may be empty)."""
    params: dict = {"timeout": timeout_secs, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(f"{_API}/getUpdates", params=params, timeout=timeout_secs + 10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", [])


def _drain_backlog() -> int:
    """Return offset = (current max update_id + 1), ignoring all pending updates.

    Called once on startup so old "stop" messages that arrived while the bot
    was offline are never acted on.
    """
    try:
        updates = _get_updates(offset=None, timeout_secs=0)
        if updates:
            return updates[-1]["update_id"] + 1
    except Exception:
        pass
    return 0


def _listener_loop(
    stop_event: threading.Event,
    log: logging.Logger,
) -> None:
    offset = _drain_backlog()
    log.debug("TelegramStopListener: drained backlog, polling from offset=%d", offset)

    while not stop_event.is_set():
        try:
            updates = _get_updates(offset, timeout_secs=25)
        except Exception as exc:
            log.debug("TelegramStopListener: getUpdates error (%s); retrying in 5 s", _redact(exc))
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message") or update.get("edited_message")
            if not msg:
                continue

            chat_id = msg.get("chat", {}).get("id")
            if chat_id != _AUTHORIZED_CHAT_ID:
                log.debug(
                    "TelegramStopListener: ignoring message from unauthorized chat %s",
                    chat_id,
                )
                continue

            text = (msg.get("text") or "").strip().lower()
            if text == "stop":
                log.info("TelegramStopListener: 'stop' received — setting stop_event")
                stop_event.set()
                try:
                    send_telegram(
                        "[study-bot] stop received — exiting after current slot completes"
                    )
                except Exception as exc:
                    log.debug("TelegramStopListener: could not send confirmation: %s", _redact(exc))
                return  # exit the listener thread

    log.debug("TelegramStopListener: stop_event already set, exiting listener")


def start_stop_listener(
    stop_event: threading.Event,
    log: logging.Logger,
) -> threading.Thread:
    """Spawn a daemon thread that sets `stop_event` on a Telegram 'stop' command.

    The thread is a daemon so it never prevents Python from exiting when the
    main loop finishes.  The caller should pass the same `stop_event` it uses
    to gate `wait_until` and the queue loop so all components exit cleanly.

    Returns the started thread (callers may .join() it if desired, but
    typically just let it run in the background).
    """
    t = threading.Thread(
        target=_listener_loop,
        args=(stop_event, log),
        name="TelegramStopListener",
        daemon=True,
    )
    t.start()
    log.info("TelegramStopListener: started (send 'stop' to halt the bot)")
    return t
