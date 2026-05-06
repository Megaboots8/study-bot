# study-bot

Checks survey response counts and sends Telegram notifications. Later will add scheduling human-approved reddit posts with survey links to survey-friendly subreddits as follows:

Human-Approved Survey Scheduler

This is a private Python/PRAW scheduler for preparing and submitting survey posts to an allowlist of survey-friendly subreddits.

The tool:
- requires human approval before posting;
- posts only from a dedicated survey-posting Reddit account;
- uses subreddit allowlists;
- enforces cooldowns and daily caps;
- logs each submitted post;
- does not vote, send DMs, scrape/sell data, manipulate karma, evade bans, or bypass subreddit rules;
- integrates with Google Sheets/MySQL only to check aggregate survey response counts before posting.

No Reddit credentials, database credentials, API keys, or private survey data are stored in this repository.

## Setup

**1. Create and activate a virtual environment**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**2. Install dependencies**

```powershell
pip install -e .
```

**3. Configure secrets**

```powershell
Copy-Item .env.example .env
```

Edit `.env` and fill in:

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
GOOGLE_SHEET_ID=your_google_sheet_id_here
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_TOKEN_FILE=token.json

SSH_HOST=your_ssh_hostname
SSH_USER=your_ssh_username
SSH_PASSWORD=your_ssh_password
SSH_PORT=22
MYSQL_HOST=your_mysql_hostname
MYSQL_PORT=3306
MYSQL_USER=your_mysql_username
MYSQL_PASSWORD=your_mysql_password
```

Make sure `credentials.json` (your Google OAuth client secrets file) is in the project root.

**4. Run**

```powershell
study-bot
```

or

```powershell
python -m study_bot
```

The **first run** opens a browser window to authorize Google Sheets access. After you approve, `token.json` is saved and subsequent runs are fully silent.

## What it does

For each configured survey:
- Reads column A of the `Form Responses 1` sheet.
- Computes `response_count = len(rows) - 1` (skips the header row).
- Sends a Telegram message: `"<Survey Name> # of responses = X"`.
- On error: sends `"[study-bot ERROR] <Survey Name>: <error details>"`.

After the survey check, for each configured experiment:
- Opens an SSH tunnel to the database server in-process (no manual tunnel needed, works after reboot).
- Queries the experiment table for total row count and `is_complete = 1` count.
- Sends a single Telegram message with both metrics and deltas vs. the last run:

```
qs_colorslider_v5 (experiment_submissions)
Total rows = 152 (increased by 1)
Complete (is_complete=1) = 88 (no change)
```

After the survey check, for each survey that has an entry in `reddit_posts.yml`:
- Opens a new tab in your default browser pointed at `https://www.reddit.com/r/<subreddit>/submit?title=...` with Reddit's officially-supported URL parameters.
- The Title field on the submit page is pre-filled.  Optional `body` text in the YAML is pre-filled into the self-text field.
- You review the post, pick a flair, and click **Post** by hand.

This uses the same `?title=...&text=...` URL contract that every "Share to Reddit" button on the web uses, so there is no browser automation, no automation fingerprints, and no Chrome-profile management.

Every run appends a timestamped entry to `logs/study-bot.log`.

## Reddit pre-fill setup

Copy the template and edit:

```powershell
Copy-Item reddit_posts.example.yml reddit_posts.yml
```

Set the `subreddit` and `title` for each survey.  The top-level key under `posts:` must match the survey `label` in `src/study_bot/config.py` exactly.  `reddit_posts.yml` is gitignored and never committed.

```yaml
posts:
  Photo Filter Preference Survey:
    subreddit: SampleSize
    title: "Which photos and filters look best? Takes about 2 minutes (Everyone)"
    body: ""                       # optional self-text body
    flair: "Academic (Repost)"     # exact flair text; requires the userscript below
    auto_click_add: true           # OS-level click on the dialog's "Add" button
```

If `reddit_posts.yml` does not exist, or a survey has no entry in it, the pre-fill step is silently skipped for that survey.

The new tab opens in whatever your OS-default browser is (typically the Chrome window you already have open and logged in).  No login flow, no first-run setup.

### Optional: auto-flair via Tampermonkey + OS-level click

The `flair` field works only if you install a tiny browser userscript (one-time setup).  The userscript runs *as you* in your normal browser when a Reddit submit URL contains a `_autoflair=...` query parameter; it opens the flair dialog on the submit page and selects the matching radio option.  Reddit ignores the parameter; only the userscript reads it.

1. Install the Tampermonkey extension in Chrome ([chrome.google.com/webstore/detail/tampermonkey](https://chrome.google.com/webstore/detail/tampermonkey/dhdgffkkebhmkfjojejmpbldmpobfkfo)) or any compatible userscript manager (Violentmonkey, Greasemonkey).
2. Open [`userscripts/reddit-auto-flair.user.js`](userscripts/reddit-auto-flair.user.js) and click "Install" / paste it into Tampermonkey.

That handles steps 1 and 2 of "click *Add flair and tags*" → "select *Academic (Repost)*" → "click *Add*".  Reddit's flair dialog uses Lit-based form-associated custom elements that ignore synthetic JavaScript clicks for the final commit step, so the userscript stops there.

The third step — clicking the dialog's blue **Add** button — is done by `study-bot`'s Python side using `pyautogui`.  When `auto_click_add: true` is set in `reddit_posts.yml`, study-bot pauses for a few seconds, takes a screenshot of the lower half of your primary monitor, finds the saturated-blue button-shaped region, and fires a real OS-level mouse click on its centroid.  Because the click comes from the operating system, Reddit accepts it and the flair is committed.

If you do not install the userscript or set `auto_click_add: false`, leave `flair: ""` in `reddit_posts.yml` (or just ignore the field) — the URL parameter is silently passed through and you pick the flair manually.

Failure modes (all silent — study-bot keeps running):

- Userscript not installed → flair dialog never opens → blue blob never appears → study-bot logs "Reddit flair Add button not located within Ns" and gives up.
- Reddit changes the flair-picker DOM → userscript logs to the browser DevTools console with the `[study-bot auto-flair]` prefix; pick the flair manually.
- Multiple unrelated blue regions in the lower half of the screen → study-bot refuses to guess and skips the click.
- `pyautogui`/`Pillow`/`numpy` not installed → study-bot logs a warning and skips the click.

While the auto-click is running (~5–15 s after the tab opens), avoid moving the mouse onto the dialog area; the screen scan picks the centroid of the blue region, so an obscuring cursor is fine but a click on something else first might race.

## Adding an experiment

Edit `EXPERIMENTS` in `src/study_bot/config.py`:

```python
EXPERIMENTS = [
    {
        "label": "qs_colorslider_v5",
        "database": "qs_colorslider_v5",
        "table": "experiment_submissions",
    },
    {
        "label": "another_experiment",
        "database": "another_db",
        "table": "experiment_submissions",
    },
]
```

The SSH/MySQL credentials in `.env` are shared across all experiments (all on the same server). Each experiment uses its own `database` name.

## Adding a survey

Edit `SURVEYS` in `src/study_bot/config.py`:

```python
SURVEYS = [
    {
        "label": "Photo Filter Preference Survey",
        "sheet_id": GOOGLE_SHEET_ID,
        "range": "'Form Responses 1'!A:A",
    },
    {
        "label": "Another Study",
        "sheet_id": "another_sheet_id",
        "range": "'Form Responses 1'!A:A",
    },
]
```
