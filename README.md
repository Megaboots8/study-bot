# study-bot

Checks survey response counts and sends Telegram notifications. Designed to grow into a full automated Reddit poster for science and marketing studies.

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

Every run appends a timestamped entry to `logs/study-bot.log`.

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
