# UID Extractor

Public Streamlit app for extracting player names and 8-digit UIDs from game profile screenshots and PDF pages, reviewing the results, and submitting all new entries directly to Google Sheets.

## Features

- Public upload page; no Google sign-in required
- Automatic OCR after selecting image or PDF files
- Per-file/per-page OCR report, including images/pages where no 8-digit UID was detected or OCR failed
- Download the specific no-UID/OCR-error images and PDF pages as `uid_ocr_issues.zip`
  - Image files are included in their original format.
  - Problematic pages in PDFs are exported as individual PNG files, not full PDFs when page rendering succeeded.
- Duplicate summary showing total repeated occurrences, repeated UID values, and source images/PDF pages for each repeated UID
- Report of extracted UIDs already present in the database, including source file/page
- Editable preview with only Name and UID columns
- One button submits all valid, new UIDs in the preview
- Duplicate UIDs are skipped against existing spreadsheet rows and within each upload batch
- CSV download of reviewed data

## Google Sheet format

The main worksheet (default name `UIDs`) must begin with these two columns:

`Name | UID`

The app writes exactly two values per new row. Source names and OCR diagnostics are displayed on the website only; they are not written to the spreadsheet.

## Google Sheets access

1. In Google Cloud Console, enable the Google Sheets API.
2. Create a service account and download its JSON key.
3. Share the spreadsheet with the service account's `client_email`, granting Editor access.
4. In Streamlit Community Cloud → app Settings → Secrets, configure:
   - `spreadsheet_id`: the spreadsheet ID
   - `worksheet_name`: `UIDs`
   - `gcp_service_account_json`: the full JSON object wrapped in TOML triple quotes
5. Keep the JSON key private. Never commit it to GitHub or paste it into chat.

See `.streamlit/secrets.example.toml` for the format.

## Upload report behaviour

- Each uploaded image is reported as a separate file.
- PDFs are reported page by page.
- Repeated UIDs are counted as duplicate occurrences beyond the first detection. The report lists the UID, where it was first found, and the additional image/page locations.
- The OCR report lists each file/page where no 8-digit UID was detected or processing failed. The ZIP download below that table contains only those reported source items. Problematic PDF pages are exported separately as PNG images, allowing only the affected pages to be downloaded.
- “No 8-digit UID detected” does not prove OCR itself failed; the image may lack a UID or the image quality/layout may make it unreadable.
- OCR can misread decorative usernames and digits, so review and correct names/UIDs before submitting.

## Public submission warning

Because the website is public and writes directly to the main spreadsheet, anyone who can use it may add data. Validation enforces an 8-digit UID, requires a name, and skips UIDs already present, but it does not fully prevent deliberate spam or incorrect submissions.

## Usage notes

- PDFs are limited to 40 pages per file.
- Each upload is limited to 15 MB.
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

Local secrets go in `.streamlit/secrets.toml`; use `.streamlit/secrets.example.toml` as a template. Never commit real credentials.

This is a starter project, not a security audit. Keep credentials private and update dependencies periodically.
