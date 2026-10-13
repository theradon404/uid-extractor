# UID Extractor

Public Streamlit app for extracting player names and 8-digit UIDs from game profile screenshots and PDF pages, reviewing/editing them, and submitting them to a Google Sheets review queue. The site owner approves pending entries into the main UID database using an admin password.

## Features

- Public upload page; no Google sign-in required
- Multiple image and PDF uploads
- Tesseract OCR
- Editable review table and CSV download
- Public submissions go to a `Pending` worksheet, not directly to the main UID database
- Password-protected admin review and approval into `UIDs`
- Deduplication against the current queue and main database during admin approval

## 1. Prepare your Google Sheet

Create a spreadsheet and add a worksheet named `UIDs` with the first row:

`Name | UID | SubmittedBy | SubmittedAt`

The app will create a second worksheet named `Pending` automatically with headers:

`Name | UID | Source | SubmittedAt | Status`

Copy the spreadsheet ID from the URL.

## 2. Configure Google Sheets access

1. In Google Cloud Console, create/select a project and enable the Google Sheets API.
2. Create a service account and download its JSON key.
3. Share the spreadsheet with the service account's `client_email`, granting Editor access.
4. Keep the JSON key private. Never commit it to GitHub or paste it into chat.

## 3. Deploy to Streamlit Community Cloud

1. Deploy this repository with `app.py` as the main file.
2. In app settings, open **Secrets** and add the following values:
   - `spreadsheet_id`: your spreadsheet ID
   - `worksheet_name`: `UIDs`
   - `admin_password`: a long random private password only you know
   - `gcp_service_account_json`: the entire downloaded service-account JSON, as a multi-line TOML triple-quoted string
3. Save Secrets and restart the app.
4. In the app's **Sharing** settings, choose **This app is public and searchable** if you want anyone with the link to access it.

## 4. Public versus admin access

- Anyone can upload images/PDFs, correct extracted names/UIDs, download CSV, and submit records for review.
- Public visitors cannot add records directly to the main UID list. Submissions go to the `Pending` worksheet.
- The site owner opens **Site owner: review submissions** in the app and enters the private `admin_password` to approve entries into `UIDs`.
- Keep the Google Sheet private and share it only with your account and the service account.

## OCR and usage notes

- OCR can misread decorative player names and digits. Review every result before submitting.
- The OCR finds any standalone 8-digit number, so tune it if screenshots contain unrelated eight-digit numbers.
- Names are guessed from nearby OCR text and may need correction.
- PDFs are capped at 40 pages per file and each upload is limited to 15 MB to help avoid exhausting free hosting resources.
- Public submissions may still be spammed. Review the Pending tab regularly and use a strong admin password.
- Files are processed in memory by this app and are not intentionally saved as files. Hosting providers may retain operational logs under their own policies.

## Local setup

Install Python 3.11 and Tesseract OCR for Windows. Ensure `tesseract.exe` is on PATH:
https://github.com/UB-Mannheim/tesseract/wiki

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Local secrets are configured with `.streamlit/secrets.toml`; see `.streamlit/secrets.example.toml` for the format. Never commit your real secrets file.

This is a starter project, not a security audit. Keep credentials private, review submissions, and update dependencies periodically.
