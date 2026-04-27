import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import GOOGLE_CREDENTIALS_FILE, GOOGLE_SCOPES, GOOGLE_TOKEN_FILE


def _get_credentials() -> Credentials:
    token_path = Path(GOOGLE_TOKEN_FILE)
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, GOOGLE_SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def get_response_count(sheet_id: str, range_a1: str) -> int:
    creds = _get_credentials()
    service = build("sheets", "v4", credentials=creds)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=range_a1)
        .execute()
    )
    values = result.get("values", [])
    return max(len(values) - 1, 0)
