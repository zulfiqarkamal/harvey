"""GAMMA7 self-assessment lead queue integration.

Reads new leads from the 'Harvey Queue' tab in the GAMMA7 Google Sheet,
creates Prospect records in Harvey's database, and marks each row processed.

**Setup (one-time):**

1. Run ``harvey gmail auth`` after this update — the Gmail OAuth scope now
   includes Google Sheets, so a fresh consent is needed.  Your Gmail
   connection is not affected; it just asks for one extra permission.
2. Share the GAMMA7 Google Sheet (ID in SHEET_ID below) with the Google
   account you used for Gmail auth (view the account under
   ``harvey gmail test``).  Editor access is needed so Harvey can write
   the ``processed`` column.
3. Add the 'Harvey Queue' tab to the sheet with the columns described in
   ``harvey/integrations/gamma7_queue_schema.md`` — or let the n8n step
   create it automatically.

**How it works:**

The n8n Assessment Intake workflow appends one row per completed assessment
to the 'Harvey Queue' tab.  Each heartbeat Harvey reads every row where
``processed`` is empty, creates a Prospect record (deduplicating on email),
and writes ``"done"`` to the ``processed`` column so the row is never
re-processed.

No email is sent by this module — the new Prospect enters Harvey's normal
pipeline and waits for the Writer to draft a campaign.
"""

import logging
import time
import json

import httpx

from harvey.models.prospect import Prospect
from harvey.state import StateManager
from harvey.integrations.gmail import _load_token, TOKEN_URL

logger = logging.getLogger("harvey.gamma7_queue")

SHEET_ID = "17M37PpqDZSmpzH799Eb6zh5HfwFpd9iitfmRW_7mtzo"
TAB_NAME = "Harvey Queue"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)

# Column order expected in the Harvey Queue tab (0-indexed)
COL_TIMESTAMP  = 0
COL_EMAIL      = 1
COL_FIRST_NAME = 2
COL_LAST_NAME  = 3
COL_FRAMEWORK  = 4
COL_SCORE      = 5
COL_TRIGGER    = 6
COL_PROCESSED  = 7

# Minimum columns required; extra columns are ignored
MIN_COLS = 7

_cached_token: tuple[str, float] | None = None   # (access_token, expires_at)


async def _sheets_token() -> str | None:
    """Short-lived Sheets access token, refreshed from the Gmail refresh token.

    Requires the Gmail token to have been obtained with the spreadsheets scope
    — this is true after re-running ``harvey gmail auth`` with the updated
    SCOPE in gmail.py.
    """
    global _cached_token
    if _cached_token and time.time() < _cached_token[1] - 60:
        return _cached_token[0]

    stored = _load_token()
    if not stored:
        logger.warning(
            "GAMMA7 queue: Gmail not authorised. Run 'harvey gmail auth' and "
            "re-authorise so the spreadsheets permission is included."
        )
        return None

    try:
        # We need GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET from the environment.
        # They are already loaded by GmailProvider; read them directly here.
        import os
        client_id = os.getenv("GMAIL_CLIENT_ID", "").strip()
        client_secret = os.getenv("GMAIL_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            logger.warning(
                "GAMMA7 queue: GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET not set in .env"
            )
            return None

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(TOKEN_URL, data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": stored["refresh_token"],
                "grant_type": "refresh_token",
                "scope": SHEETS_SCOPE,
            })
    except httpx.HTTPError as e:
        logger.warning(f"GAMMA7 queue: token refresh failed: {e}")
        return None

    if resp.status_code != 200:
        logger.warning(
            f"GAMMA7 queue: token refresh rejected ({resp.status_code}). "
            "Re-run 'harvey gmail auth' to include the Sheets permission."
        )
        return None

    try:
        data = resp.json()
    except ValueError:
        logger.warning("GAMMA7 queue: token refresh returned unreadable JSON")
        return None

    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        logger.warning("GAMMA7 queue: token refresh returned no access_token")
        return None

    try:
        expires_in = float(data.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600.0

    _cached_token = (token, time.time() + expires_in)
    return token


async def _sheets_get(token: str, path: str, **params) -> dict | None:
    """GET a Sheets API resource; returns the parsed JSON or None on error."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                f"{SHEETS_API}/{SHEET_ID}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
    except httpx.HTTPError as e:
        logger.warning(f"GAMMA7 queue: Sheets GET {path} failed: {e}")
        return None

    if resp.status_code == 403:
        logger.warning(
            "GAMMA7 queue: permission denied (403). Share the GAMMA7 Google Sheet "
            "with the Google account used for Gmail auth (Editor access)."
        )
        return None
    if resp.status_code != 200:
        logger.warning(f"GAMMA7 queue: Sheets GET {path} → {resp.status_code}: {resp.text[:200]}")
        return None
    try:
        return resp.json()
    except ValueError:
        logger.warning(f"GAMMA7 queue: Sheets GET {path} returned unreadable JSON")
        return None


async def _sheets_put(token: str, path: str, body: dict) -> bool:
    """PUT to a Sheets API endpoint; returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.put(
                f"{SHEETS_API}/{SHEET_ID}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(body).encode(),
            )
    except httpx.HTTPError as e:
        logger.warning(f"GAMMA7 queue: Sheets PUT {path} failed: {e}")
        return False

    if not (200 <= resp.status_code < 300):
        logger.warning(f"GAMMA7 queue: Sheets PUT {path} → {resp.status_code}: {resp.text[:200]}")
        return False
    return True


def _personalization_note(row: list[str]) -> str:
    """Build the personalization_notes string from a queue row."""
    framework = row[COL_FRAMEWORK] if len(row) > COL_FRAMEWORK else ""
    score = row[COL_SCORE] if len(row) > COL_SCORE else ""
    trigger = row[COL_TRIGGER] if len(row) > COL_TRIGGER else ""

    parts = []
    if framework and score:
        parts.append(f"{framework}: {score}")
    elif framework:
        parts.append(framework)
    if trigger:
        parts.append(f"trigger: {trigger}")
    return " — ".join(parts)


async def process_queue(state: StateManager) -> int:
    """Read new rows from the Harvey Queue tab and add them as Prospects.

    Returns the number of rows processed this call (0 when nothing was new,
    the sheet is unreachable, or Gmail auth hasn't been updated yet).

    Errors are logged at WARNING level and never propagate — a Sheets
    failure must never take down Harvey's heartbeat loop.
    """
    try:
        return await _process_queue_inner(state)
    except Exception as e:
        logger.warning(f"GAMMA7 queue: unexpected error (skipping this cycle): {e}", exc_info=True)
        return 0


async def _process_queue_inner(state: StateManager) -> int:
    token = await _sheets_token()
    if not token:
        return 0

    # Read all values from the Harvey Queue tab
    range_ref = f"{TAB_NAME}!A:H"
    data = await _sheets_get(token, f"/values/{range_ref}")
    if data is None:
        return 0

    rows: list[list[str]] = data.get("values", [])
    if not rows:
        logger.debug("GAMMA7 queue: Harvey Queue tab is empty or missing")
        return 0

    # First row is the header — skip it
    header = rows[0] if rows else []
    data_rows = rows[1:]

    if not data_rows:
        logger.debug("GAMMA7 queue: no data rows in Harvey Queue tab")
        return 0

    processed_count = 0
    updates = []  # (row_index_1_based, range_a1) pairs to write "done"

    for i, row in enumerate(data_rows):
        # Rows are 2-indexed in A1 notation (1-based, plus 1 for the header)
        sheet_row = i + 2

        # Pad short rows so index access is safe
        while len(row) <= COL_PROCESSED:
            row.append("")

        # Skip already-processed rows
        processed_cell = row[COL_PROCESSED].strip().lower()
        if processed_cell in ("done", "true", "1", "yes"):
            continue

        email = row[COL_EMAIL].strip().lower()
        if not email:
            logger.debug(f"GAMMA7 queue: row {sheet_row} has no email — skipping")
            updates.append((sheet_row, "done"))
            continue

        first_name = row[COL_FIRST_NAME].strip()
        last_name  = row[COL_LAST_NAME].strip()
        notes      = _personalization_note(row)

        prospect = Prospect(
            first_name=first_name,
            last_name=last_name,
            email=email,
            source="gamma7-assessment",
            # title is required for is_valid(); use a placeholder that the
            # Writer will improve during personalization
            title="Business owner",
            personalization_notes=notes,
            status="new",
        )

        prospect_id = await state.add_prospect(prospect)
        logger.info(
            f"GAMMA7 queue: row {sheet_row} → prospect {prospect_id} "
            f"({email}, {notes or 'no notes'})"
        )
        updates.append((sheet_row, "done"))
        processed_count += 1

    # Write "done" to all processed rows in a single batch update
    if updates:
        value_ranges = [
            {
                "range": f"{TAB_NAME}!H{row_num}",
                "values": [["done"]],
            }
            for row_num, _ in updates
        ]
        body = {
            "valueInputOption": "RAW",
            "data": value_ranges,
        }
        ok = await _sheets_put(
            token,
            "/values:batchUpdate",
            body,
        )
        if not ok:
            # Partial failure: Harvey added the prospects but couldn't mark rows.
            # They will be re-processed next cycle. add_prospect deduplicates on
            # email so the duplicate inserts are silently ignored — safe to retry.
            logger.warning(
                f"GAMMA7 queue: {len(updates)} row(s) processed but could not "
                "be marked done in the sheet — will retry next cycle"
            )

    if processed_count:
        logger.info(f"GAMMA7 queue: {processed_count} new lead(s) added to Harvey")
    return processed_count
