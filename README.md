# UID Extractor

Public Streamlit app for extracting player names and 8-digit UIDs from game profile screenshots and PDF pages, reviewing the results, and submitting all new entries directly to Google Sheets.

## Features

- Public upload page; no Google sign-in required
- Automatic OCR after selecting image or PDF files
- Editable preview with only Name and UID columns
- One button submits all valid, new UIDs in the preview
- Duplicate UIDs are skipped against existing spreadsheet rows and within each upload batch
- CSV download of reviewed data
- Image uploads and page-by-page PDF processing

## 1. Prepare your Google Sheet

Create a spreadsheet with a worksheet named `UIDs`. The first two cells in row 1 should be:

`Name | UID`

The app writes exactly two values per new row: player name and UID. If the worksheet already has extra columns from an earlier version, the app does not delete existing data in those columns; you can remove the old extra columns manually if you want the sheet to display only Name and UID.

The configured service account must have Editor access to the spreadsheet.

## 2. Configure Google Sheets access

1. In Google Cloud Console, create/select a project and enable the Google Sheets API.
2. Create a service account and download its JSON key.
3. Share the spreadsheet with the service account's `client_email`, granting Editor access.
4. Keep the JSON key private. Never commit it to GitHub or paste it into chat.

## 3. Configure Streamlit Secrets

In Streamlit Community Cloud, open the app's menu and go to **Settings → Secrets**. Add:
- `spreadsheet_id`: your spreadsheet ID
- `worksheet_name`: `UIDs`
- `gcp_service_account_json`: the full downloaded service-account JSON object wrapped as a multi-line TOML triple-quoted string. See `.streamlit/secrets.example.toml`.

Do not put the real JSON key in the repository. Save the Secrets and restart the app.

## 4. Publish the app

Deploy this repository to Streamlit Community Cloud with `app.py` as the main file. In the app's sharing settings, choose public access if anyone with the link should be able to use it.

## How direct submission works

1. A visitor uploads one or more screenshots or PDFs.
2. OCR runs automatically and the review table appears.
3. The visitor can correct names or UIDs; there is no per-row selection.
4. Clicking **Submit all new UIDs to database** sends every valid, new row in the table to the `UIDs` worksheet.

Only `Name` and `UID` are written. Existing UIDs are skipped. The app does not use a Pending queue or owner approval step.

## Important security note

Because the website is public and writes directly to the main spreadsheet, anyone who can use it may add data. Validation enforces an 8-digit UID, requires a name, and skips UIDs already present, but it does not fully prevent deliberate spam or incorrect submissions. If abuse becomes an issue, add stronger access controls or rate limiting.

## OCR and usage notes

- OCR can misread decorative usernames and digits; review every row before submission.
- The OCR finds any standalone 8-digit number, so it may need tuning if screenshots contain unrelated eight-digit numbers.
- Player names are guessed from nearby OCR lines and may need correction.
- PDFs are limited to 40 pages per file and uploads to 15 MB per file to help avoid exhausting free hosting resources.
- Uploaded files are processed in memory by the app and are not intentionally saved as files. Hosting providers may have their own operational logs and retention policies.

## Local setup

Install Python 3.11 and Tesseract OCR for Windows. Ensure `tesseract.exe` is on PATH:
https://github.com/UB-Mannheim/tesseract/wiki

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Local secrets go in `.streamlit/secrets.toml`; use `.streamlit/secrets.example.toml` as a template. Never commit a real secrets file.

This is a starter project, not a security audit. Keep credentials private and update dependencies periodically.
