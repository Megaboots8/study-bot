# study-bot — Windows always-on setup

This doc walks through making study-bot a reliable always-on poster on a
Windows laptop:

1. Disable sleep / hibernate so the schedule actually fires.
2. Auto-start study-bot at logon, with restart-on-failure and wake-from-
   sleep.
3. Make Google OAuth refresh tokens long-lived (no more weekly re-auth).
4. Pre-flight checks before the first scheduled day.

Run all PowerShell commands from an Administrator PowerShell unless noted.

---

## 1. Power plan

study-bot is a single Python process that waits for each slot then fires.
If Windows sleeps the laptop the wait silently pauses; when it wakes,
the script catches up to whatever slot is closest, but slots that were
strictly in the past while asleep are missed.

### A. Never sleep on AC

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 15   # screen can still turn off, fine
```

`0` means "never". The screen turning off is fine; the OS itself stays
awake.

### B. Disable hibernation entirely (optional but recommended)

```powershell
powercfg /hibernate off
```

This also frees the `hiberfil.sys` disk space.

### C. (Laptop on battery) decide what you want

If you sometimes use the laptop unplugged and want study-bot to keep
posting then:

```powershell
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-dc 0
```

If you'd rather the laptop sleep on battery and only post when plugged
in, leave the DC timeouts as they are. Task Scheduler's "Wake the
computer to run this task" option (below) wakes the machine at each
slot regardless.

### D. (Optional) lid-close behaviour

If you want to close the lid at night and still have study-bot post:

```powershell
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
```

`0` = "Do nothing" on lid close.

---

## 2. Auto-start at logon (Task Scheduler)

Task Scheduler is preferred over a Startup-folder shortcut because it
can:

- Restart the task if Python crashes.
- Wake the laptop to run the task even if it's asleep.
- Run hidden / minimised.
- Re-fire on missed schedule.

### Create the task

1. Press `Win + R`, type `taskschd.msc`, press Enter.
2. In the right pane, click **Create Task...** (NOT "Create Basic Task").
3. **General** tab:
   - Name: `study-bot`
   - "Run only when user is logged on" (default — Chrome must be
     reachable, so this needs to be a logged-on user session).
   - Configure for: Windows 10 / Windows 11.
4. **Triggers** tab → **New...**:
   - Begin the task: **At log on**
   - Specific user: pick your account
   - Delay task for: **1 minute** (lets Wi-Fi + Chrome finish coming up)
   - OK
5. **Actions** tab → **New...**:
   - Action: **Start a program**
   - Program/script: `C:\Users\megab\OneDrive\Desktop\Work\study-bot\run-study-bot.bat`
   - Start in: `C:\Users\megab\OneDrive\Desktop\Work\study-bot`
   - OK
6. **Conditions** tab:
   - Tick **Wake the computer to run this task**.
   - Untick **Start the task only if the computer is on AC power** if you
     want it to run on battery as well.
7. **Settings** tab:
   - Tick **Allow task to be run on demand**.
   - Tick **Run task as soon as possible after a scheduled start is missed**.
   - Tick **If the task fails, restart every: 1 minute** up to **5 times**.
   - "If the task is already running, then the following rule applies:"
     → **Do not start a new instance**.
8. Save (you'll be prompted for your Windows password — needed for the
   wake-on-task option).

### Verify

- Right-click the `study-bot` task → **Run**. A cmd window should open,
  show the heartbeat log, and you should get a Telegram message like
  `[study-bot] started (20 slots, jitter ±120s) ...`.
- Stop it (close the window). Log out and back in to test the
  "At log on" trigger.

---

## 3. Google OAuth: stop the weekly re-auth

By default, OAuth refresh tokens for an app whose consent screen is in
**Testing** mode expire after **7 days**. After that, study-bot's
Google-Sheets call hits `invalid_grant: Token has been expired or
revoked` and you need to re-run the local-server flow.

The fix is to push your Cloud Console OAuth consent screen to
**Production**. For a personal app used only by yourself, no Google
verification review is required as long as you stay within the
non-sensitive scopes you already use (`spreadsheets.readonly`).

### Steps

1. Open <https://console.cloud.google.com/apis/credentials/consent>
2. Pick the project that owns `credentials.json` (top-left selector).
3. Under **Publishing status** click **Publish App** → **Confirm**.
4. The status will switch from "Testing" to "In production".
5. Delete your existing token file so the next study-bot run forces a
   fresh OAuth flow under the production-mode app:

   ```powershell
   Remove-Item C:\Users\megab\OneDrive\Desktop\Work\study-bot\token.json
   ```

6. Run study-bot once interactively to redo the OAuth consent:

   ```powershell
   cd C:\Users\megab\OneDrive\Desktop\Work\study-bot
   .\.venv\Scripts\activate.ps1
   python -m study_bot run --now --dry-run --only survey
   ```

   - Browser opens, asks to grant the read-only Sheets scope, completes.
   - `token.json` is rewritten with a refresh token that no longer
     expires after 7 days.

### Why this is safe

- The only OAuth scope used is
  `https://www.googleapis.com/auth/spreadsheets.readonly`. That is a
  non-sensitive scope and does NOT trigger Google's verification review
  even after publishing.
- Your app keeps the same client ID and `credentials.json`. Only the
  consent-screen status changed.
- The "unverified app" warning that appeared in the consent flow when
  the app was in Testing also disappears.

### Token-refresh failure handling in study-bot

If the refresh ever does fail (revocation, account password change,
revoked OAuth grant in your Google account, etc.) study-bot now sends a
Telegram message tagged `[study-bot ERROR] ...` and continues to the
next slot. Re-run the script interactively to redo OAuth.

---

## 4. Pre-flight checks before the first scheduled day

Before letting study-bot run unattended:

1. **Chrome is the system default browser.**
   - Settings → Apps → Default apps → Web browser → Google Chrome.

2. **u/Fair_Imagination_410 stays signed in to Reddit on Chrome.**
   - chrome://settings/passwords/ should have your reddit.com password
     saved with "Sign in automatically".

3. **Tampermonkey is enabled** and the userscript shows v0.8.3 (or
   newer) with the toggle ON.
   - chrome://extensions/ → Tampermonkey → Details → "Allow access to
     file URLs" off (default), "Site access" set to "On all sites".
   - Click the Tampermonkey icon → Dashboard → ensure
     "study-bot Reddit auto-flair + auto-post (title-IPC)" toggle is
     enabled.

4. **`reddit_posts.yml` exists and is correct.** This file is
   gitignored. Compare with `reddit_posts.example.yml`.

5. **`.env` exists with all required secrets.** Compare with
   `.env.example`.

6. **Dry-run smoke test (no posts submitted):**

   ```powershell
   python -m study_bot run --now --dry-run
   ```

   - You'll get one Telegram per study/subreddit pair, prefixed
     `[DRY-RUN]`, plus per-slot Reddit submit tabs that you can
     visually inspect (none of them auto-submit because dry-run forces
     auto_click_add and auto_post off).
   - Close those tabs after inspection.
   - This also exercises Sheets + MySQL so you'll know whether OAuth /
     SSH tunnel are healthy before Monday.

7. **Live single-fire test (one real post, off-schedule):** if you want
   to fully verify the OS-click flow before Monday, temporarily edit
   `reddit_posts.yml` to give one post a `schedule` slot a couple of
   minutes in the future, then run:

   ```powershell
   python -m study_bot run --only survey
   ```

   Don't forget to restore the original schedule afterwards.

---

## 5. Day-to-day operations

- **Logs:** `logs/study-bot.log`. Rolls per-run.
- **Remote stop:** send the word `stop` (any capitalisation, surrounding
  whitespace OK) as a Telegram message to your bot. study-bot replies
  with a confirmation and exits cleanly (exit code 0) after the current
  slot finishes. Task Scheduler's restart rule does **not** fire on a
  clean exit, so the bot stays stopped.  To restart it, right-click the
  task → **Run**, or reboot.
- **Local stop:** close the cmd window, or kill the python process in
  Task Manager. A non-zero exit code triggers Task Scheduler's restart
  rule (if configured), which respawns the bot within a minute.
- **Pause for a day:** in Task Scheduler, right-click `study-bot` →
  **Disable**. Re-enable when you want it back.
- **Edit the schedule:** edit `reddit_posts.yml`, then either let the
  current week finish and the next loop iteration pick up the changes,
  or restart the task to pick them up immediately.
- **Add a new study:** add a SURVEY/EXPERIMENT entry in
  `src/study_bot/config.py` AND a matching entry in `reddit_posts.yml`.

---

## 6. Telegram message format

### Startup heartbeat (sent once on process start)

```
[study-bot] started (20 slots, jitter ±120s)
First slot: Mon 2026-05-11 12:05 EDT
Send 'stop' here to halt the bot
```

### Per-slot combined message (sent after each slot fires)

Success:

```
Photo Filter Preference Survey # of responses = 173 (increased by 1)
Posted: r/SampleSize OK
```

Posting failure:

```
[study-bot ERROR] Photo Filter Preference Survey # of responses = 173 (increased by 1)
Posted: r/SampleSize FAILED — Add button not clicked (Reddit window not foreground or timeout)
```

Count API failure (post is skipped for this slot):

```
[study-bot ERROR] Photo Filter Preference Survey: <error details>
```

Dry-run:

```
[DRY-RUN] Photo Filter Preference Survey # of responses = 173 (no change)
Posted: r/SampleSize SKIPPED (dry-run)
```

### Remote stop confirmation

```
[study-bot] stop received — exiting after current slot completes
```

### Crash

```
[study-bot CRASH] <exception message>
```

---

## 7. Known-failure quick reference

| Symptom in Telegram                                             | Likely cause                                        | Fix                                                                |
|-----------------------------------------------------------------|-----------------------------------------------------|--------------------------------------------------------------------|
| `[study-bot ERROR] ... invalid_grant`                           | Google refresh token revoked / 7-day testing expiry | Republish consent screen (section 3); delete `token.json`; rerun. |
| `[study-bot ERROR] ... 2003 Can't connect to MySQL`             | DreamHost SSH tunnel rejected                       | DreamHost SSH credentials may have rotated; check `.env`.          |
| `Posted: r/X FAILED — tab open failed`                          | Chrome not running, or default browser is wrong     | Open Chrome; reset default browser per section 4 step 1.           |
| `Posted: r/X FAILED — Add button not clicked ...`               | Tampermonkey disabled, userscript out of date, or Chrome lost focus | Enable Tampermonkey; reload userscript. study-bot now tries to bring Chrome to front automatically. |
| `Posted: r/X FAILED — Post button not clicked`                  | Flair Add succeeded but Post timed out              | Usually a slow Reddit page load. Retry next slot; post manually if urgent. |
| `[study-bot CRASH] ...`                                         | Unhandled exception at startup                      | Read traceback in cmd window; common causes: missing env var, broken yaml. |
