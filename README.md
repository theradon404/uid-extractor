# UID Extractor

Private Streamlit app to extract player names and 8-digit UIDs from game profile screenshots and PDFs, review/edit the results, and save approved entries to a private Google Sheet.

## Features

- Google sign-in plus an explicit email allowlist
- Multiple image and PDF uploads
- Tesseract OCR
- Editable review table before saving
- Duplicate detection against the existing sheet and current upload batch
- Append approved entries with submitter and UTC timestamp

## 1. Create a Google Sheet

Create a spreadsheet with a worksheet named `UIDs`. Its first row must be:

`Name | UID | SubmittedBy | SubmittedAt`

Copy the spreadsheet ID from the URL.

## 2. Configure Google Sheets access

1. In Google Cloud Console, create/select a project and enable the Google Sheets API.
2. Create a service account and a JSON key.
3. Share the spreadsheet with the service account's `client_email`, granting Editor access.
4. Keep the key private. Put the service account fields in Streamlit secrets.

## 3. Configure Google login

Follow the Streamlit guide: https://docs.streamlit.io/develop/tutorials/authentication/google

Create an OAuth web client and add these authorized redirect URIs:
- Local: `http://localhost:8501/oauth2callback`
- Deployed: `https://YOUR-APP-NAME.streamlit.app/oauth2callback`

Add permitted email addresses to the root-level `allowed_emails` list in secrets. If the OAuth consent screen is in Testing mode, add these accounts as Google test users too.

## 4. Run locally on Windows

Install Python 3.11. Install Tesseract OCR for Windows and ensure `tesseract.exe` is on PATH: https://github.com/UB-Mannheim/tesseract/wiki

In PowerShell, from the project folder:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .streamlit\secrets.example.toml .streamlit\secrets.toml
```

Edit `.streamlit/secrets.toml` with real credentials, then run:

```powershell
streamlit run app.py
```

## 5. Deploy to Streamlit Community Cloud

1. Create a private GitHub repository.
2. Commit `app.py`, `requirements.txt`, `packages.txt`, `.gitignore`, `README.md`, and the example secrets template. Never commit `.streamlit/secrets.toml`.
3. Open https://share.streamlit.io/ and deploy the repository with `app.py` as the entry point.
4. In app settings, open **Secrets** and paste your configured TOML secrets.
5. Add the deployed URL plus `/oauth2callback` to the Google OAuth client's authorized redirect URIs.
6. Restart and test sign-in, image upload, PDF upload, duplicate checking, and saving.

Streamlit Community Cloud installs Linux system packages listed in `packages.txt`, including Tesseract here.

## Important limitations

- OCR may misread decorative names or digits. Always review results before saving.
- The extractor looks for any standalone 8-digit number, so it may need tuning if the screenshot has unrelated 8-digit numbers.
- Name extraction guesses from nearby OCR lines; correct names in the review table.
- Community Cloud has resource and usage limits. Start with smaller PDF batches.
- Uploaded files are processed in memory by this app and are not intentionally saved to disk. Hosting providers may still have operational logs or retention policies.
- Keep the spreadsheet private and share it only with your account and the service account.
- This is a starter implementation, not a security audit. Never expose OAuth or service-account secrets.
