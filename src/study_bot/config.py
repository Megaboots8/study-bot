import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

SURVEYS = [
    {
        "label": "Photo Filter Preference Survey",
        "sheet_id": GOOGLE_SHEET_ID,
        "range": "'Form Responses 1'!A:A",
    },
]
