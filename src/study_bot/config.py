import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

SSH_HOST = os.environ["SSH_HOST"]
SSH_USER = os.environ["SSH_USER"]
SSH_PASSWORD = os.environ["SSH_PASSWORD"]
SSH_PORT = int(os.getenv("SSH_PORT", "22"))

MYSQL_HOST = os.environ["MYSQL_HOST"]
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ["MYSQL_USER"]
MYSQL_PASSWORD = os.environ["MYSQL_PASSWORD"]

SURVEYS = [
    {
        "label": "Photo Filter Preference Survey",
        "sheet_id": GOOGLE_SHEET_ID,
        "range": "'Form Responses 1'!A:A",
    },
]

EXPERIMENTS = [
    {
        "label": "qs_colorslider_v5",
        "database": "qs_colorslider_v5",
        "table": "experiment_submissions",
    },
]
