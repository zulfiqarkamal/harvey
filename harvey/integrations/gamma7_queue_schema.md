# Harvey Queue — Google Sheet Tab Schema

The n8n **Assessment Intake** workflow appends one row per completed GAMMA7
self-assessment to this tab.  Harvey reads every unprocessed row each heartbeat,
creates a Prospect record, and writes `"done"` to the `processed` column so the
row is never re-processed.

## Column layout (A–H, 0-indexed)

| Col | Letter | Name         | Example value                     | Notes |
|-----|--------|--------------|-----------------------------------|-------|
| 0   | A      | timestamp    | `2026-11-06T09:14:00Z`            | ISO 8601, set by n8n |
| 1   | B      | email        | `owner@example.com`               | Required — rows with no email are skipped and marked done |
| 2   | C      | first_name   | `Alex`                            | Used as prospect first name |
| 3   | D      | last_name    | `Smith`                           | Used as prospect last name |
| 4   | E      | framework    | `NIS2`                            | Assessment framework completed |
| 5   | F      | score        | `42%`                             | Maturity score or phase label |
| 6   | G      | trigger      | `low_score`                       | Optional — why Harvey should follow up |
| 7   | H      | processed    | *(empty → pending, `done` → done)*| Harvey writes `"done"` here |

## Processed column values

Harvey recognises these values in column H as already-processed and skips the row:
`done`, `true`, `1`, `yes` (case-insensitive).  Any other value (including empty)
is treated as pending.

## Personalization notes

Harvey combines columns E–G into a `personalization_notes` string on the Prospect
record, e.g. `"NIS2: 42% — trigger: low_score"`.  The Writer uses this when
drafting outreach for the prospect.

## Setup

1. Add a tab named exactly **Harvey Queue** to the GAMMA7 Google Sheet
   (`17M37PpqDZSmpzH799Eb6zh5HfwFpd9iitfmRW_7mtzo`).
2. Row 1 must be a header row (Harvey skips it automatically).
3. Share the sheet with the Google account used for Gmail auth (Editor access).
4. Re-run `harvey gmail auth` once so the token includes the Sheets permission.

The n8n Assessment Intake workflow handles writing new rows; Harvey handles
reading and marking them done.
