"""Pull every tab of the Google Sheet and upload one raw JSON snapshot to a Databricks Volume.

Runs in GitHub Actions (or locally). It does NOT clean anything: bronze keeps the sheet exactly
as it is, so you can always re-run the cleaning later from the raw data.

Each tab is fetched three ways, because each answers a different question later:
  raw       -> UNFORMATTED_VALUE + SERIAL_NUMBER dates: real numbers, dates as day counts (unambiguous)
  formatted -> what you see on screen ("1/10/2026", "S$5.70"): useful to debug date mix-ups
  formula   -> the formulas themselves: shows hand-made rules like =SUM(E2:E147)-E110

Environment variables (set as GitHub Secrets, or in a local .env you never commit):
  GOOGLE_SA_JSON    full JSON key of the Google service account
  SHEET_ID          the long id in the sheet URL: docs.google.com/spreadsheets/d/<SHEET_ID>/edit
  DATABRICKS_HOST   https://dbc-....cloud.databricks.com
  DATABRICKS_TOKEN  personal access token
  CATALOG           optional, defaults to "workspace"
"""

from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
RENDERS = {
    "raw": {"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"},
    "formatted": {"valueRenderOption": "FORMATTED_VALUE"},
    "formula": {"valueRenderOption": "FORMULA"},
}


def fetch_snapshot(sheet_id: str, sa_info: dict) -> dict:
    creds = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()

    meta = sheets.get(spreadsheetId=sheet_id, fields="properties.title,sheets.properties").execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]
    # Quote tab names: tabs like "1026" would otherwise be read as a cell reference.
    ranges = ["'" + t.replace("'", "''") + "'" for t in titles]

    tabs = {t: {"title": t, "index": i} for i, t in enumerate(titles)}
    for render_name, options in RENDERS.items():
        resp = sheets.values().batchGet(spreadsheetId=sheet_id, ranges=ranges, **options).execute()
        for title, value_range in zip(titles, resp.get("valueRanges", [])):
            tabs[title][render_name] = value_range.get("values", [])

    return {
        "snapshot_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spreadsheet_id": sheet_id,
        "spreadsheet_title": meta["properties"]["title"],
        "tabs": list(tabs.values()),
    }


def upload_snapshot(snapshot: dict, catalog: str) -> str:
    w = WorkspaceClient()  # reads DATABRICKS_HOST / DATABRICKS_TOKEN from the environment
    folder = f"/Volumes/{catalog}/bronze/raw/google_sheets"
    stamp = snapshot["snapshot_ts"].replace(":", "").replace("-", "").replace("+0000", "Z")
    path = f"{folder}/snapshot_{stamp}.json"
    w.files.create_directory(folder)  # idempotent, like mkdir -p
    data = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
    w.files.upload(path, io.BytesIO(data), overwrite=False)
    return path


def main() -> None:
    try:  # local runs: read secrets from a .env file (GitHub Actions passes them as env vars instead)
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    sa_info = json.loads(os.environ["GOOGLE_SA_JSON"])
    snapshot = fetch_snapshot(os.environ["SHEET_ID"], sa_info)
    n_rows = sum(len(t.get("raw", [])) for t in snapshot["tabs"])
    print(f"Fetched {len(snapshot['tabs'])} tabs, {n_rows} rows from '{snapshot['spreadsheet_title']}'")
    path = upload_snapshot(snapshot, os.environ.get("CATALOG", "workspace"))
    print(f"Uploaded {path}")


if __name__ == "__main__":
    main()
