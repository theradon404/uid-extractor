import io
import json
import re
from datetime import datetime, timezone

import fitz
import gspread
import pandas as pd
import pytesseract
import streamlit as st
from google.oauth2.service_account import Credentials
from PIL import Image, ImageEnhance, ImageOps

st.set_page_config(page_title="UID Extractor", page_icon="🔎", layout="wide")
st.title("🔎 UID Extractor")
st.caption("Upload game profile screenshots or PDFs, review the names and UIDs, then save approved entries.")

# Authentication: Google sign-in plus an explicit approved-email allowlist.
ALLOWED_EMAILS = {
    str(value).strip().lower()
    for value in st.secrets.get("allowed_emails", [])
    if str(value).strip()
}

if not st.user.is_logged_in:
    st.info("This website is private. Sign in with an approved Google account.")
    st.button("Sign in with Google", on_click=st.login)
    st.stop()

USER_EMAIL = str(getattr(st.user, "email", "") or "").strip().lower()
if not USER_EMAIL or USER_EMAIL not in ALLOWED_EMAILS:
    st.error("This Google account is not on the approved-user list.")
    st.button("Sign out", on_click=st.logout)
    st.stop()

with st.sidebar:
    st.success(f"Signed in: {USER_EMAIL}")
    st.button("Sign out", on_click=st.logout)

UID_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")
IGNORED_NAMES = {
    "uid", "id", "level", "lvl", "profile", "player profile", "copy",
    "server", "guild", "rank", "power", "close"
}


@st.cache_resource
def get_sheets_client():
    credentials = Credentials.from_service_account_info(
        json.loads(st.secrets["gcp_service_account_json"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return gspread.authorize(credentials)


def get_worksheet():
    spreadsheet = get_sheets_client().open_by_key(st.secrets["spreadsheet_id"])
    worksheet_name = st.secrets.get("worksheet_name", "UIDs")
    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=4)
        worksheet.append_row(["Name", "UID", "SubmittedBy", "SubmittedAt"])

    values = worksheet.get_all_values()
    if not values:
        worksheet.append_row(["Name", "UID", "SubmittedBy", "SubmittedAt"])
    elif values[0][:2] != ["Name", "UID"]:
        raise ValueError(
            f'The first row of "{worksheet_name}" must begin with Name, UID. '
            "Use a dedicated worksheet with headers Name, UID, SubmittedBy, SubmittedAt."
        )
    return worksheet


def get_existing_uids(worksheet):
    values = worksheet.get_all_values()
    existing = set()
    for row in values[1:]:
        if len(row) > 1:
            match = UID_RE.search(str(row[1]))
            if match:
                existing.add(match.group(1))
    return existing


def preprocess_image(image):
    image = ImageOps.exif_transpose(image).convert("RGB")
    if image.width < 1200:
        scale = min(3, 1200 / image.width)
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    image = ImageOps.grayscale(image)
    return ImageEnhance.Contrast(image).enhance(1.7)


def looks_like_name(line):
    cleaned = re.sub(r"\s+", " ", line).strip(" |:：#-—")
    low = cleaned.lower()
    if not 2 <= len(cleaned) <= 36:
        return False
    if low in IGNORED_NAMES or UID_RE.search(cleaned):
        return False
    if re.search(r"\b(level|server|power|rank|uid|id)\b", low):
        return False
    return sum(char.isdigit() for char in cleaned) <= 3


def extract_image(image, source):
    text = pytesseract.image_to_string(preprocess_image(image), config="--psm 6")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    results, seen = [], set()

    for index, line in enumerate(lines):
        for uid in UID_RE.findall(line):
            if uid in seen:
                continue
            seen.add(uid)

            # Guess the name from the closest plausible OCR line.
            player_name = ""
            for neighbor in (index - 1, index + 1, index - 2, index + 2):
                if 0 <= neighbor < len(lines) and looks_like_name(lines[neighbor]):
                    player_name = re.sub(r"\s+", " ", lines[neighbor]).strip()
                    break

            results.append({
                "Name": player_name,
                "UID": uid,
                "Source": source,
                "Approve": True,
            })
    return results


def process_upload(upload):
    file_data = upload.getvalue()
    results = []

    if upload.name.lower().endswith(".pdf"):
        document = fitz.open(stream=file_data, filetype="pdf")
        for page_index, page in enumerate(document):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.3, 2.3), alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            results.extend(extract_image(image, f"{upload.name} — page {page_index + 1}"))
    else:
        image = Image.open(io.BytesIO(file_data))
        results.extend(extract_image(image, upload.name))

    return results


uploads = st.file_uploader(
    "Choose profile images or PDFs",
    type=["png", "jpg", "jpeg", "webp", "pdf"],
    accept_multiple_files=True,
    help="You can select several screenshots and PDF files at once.",
)

if "review_data" not in st.session_state:
    st.session_state.review_data = None

if st.button("Extract UIDs", type="primary", disabled=not uploads):
    extracted = []
    progress = st.progress(0)
    for index, upload in enumerate(uploads):
        try:
            extracted.extend(process_upload(upload))
        except Exception as exc:
            st.warning(f"Could not process {upload.name}: {exc}")
        progress.progress((index + 1) / len(uploads))
    progress.empty()

    # Keep the first result for each UID across this entire upload batch.
    unique_rows, seen_batch = [], set()
    for row in extracted:
        if row["UID"] not in seen_batch:
            seen_batch.add(row["UID"])
            unique_rows.append(row)

    st.session_state.review_data = pd.DataFrame(
        unique_rows, columns=["Name", "UID", "Source", "Approve"]
    )
    if not unique_rows:
        st.warning("No 8-digit UID was detected. Try a clearer screenshot or crop the profile area.")

if st.session_state.review_data is not None:
    st.subheader("Review extracted entries")
    try:
        sheet = get_worksheet()
        existing = get_existing_uids(sheet)
    except Exception as exc:
        sheet, existing = None, set()
        st.error(f"Google Sheets connection issue: {exc}")

    review = st.session_state.review_data.copy()
    review["UID"] = review["UID"].astype(str).str.strip()
    review["Already in sheet"] = review["UID"].isin(existing)
    review["Repeated in upload"] = review["UID"].duplicated(keep="first")
    review.loc[review["Already in sheet"] | review["Repeated in upload"], "Approve"] = False

    st.write(
        f"Detected **{len(review)}** UIDs. "
        f"Already present in the spreadsheet: **{int(review['Already in sheet'].sum())}**."
    )
    st.caption("Edit the name or UID, then tick Approve for every entry you want to save.")

    edited = st.data_editor(
        review,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_config={
            "Name": st.column_config.TextColumn("Player name"),
            "UID": st.column_config.TextColumn("8-digit UID", help="Must be exactly 8 digits."),
            "Source": st.column_config.TextColumn("Source file/page", disabled=True),
            "Approve": st.column_config.CheckboxColumn("Approve"),
            "Already in sheet": st.column_config.CheckboxColumn("Already in sheet", disabled=True),
            "Repeated in upload": st.column_config.CheckboxColumn("Repeated in upload", disabled=True),
        },
        disabled=["Source", "Already in sheet", "Repeated in upload"],
        key="uid_review_editor",
    )

    if st.button("Approve and save to Google Sheets", type="primary", disabled=sheet is None):
        known = set(existing)
        rows_to_save, errors = [], []

        for _, row in edited.iterrows():
            if not bool(row.get("Approve", False)):
                continue

            name = str(row.get("Name", "")).strip()
            uid = re.sub(r"\s+", "", str(row.get("UID", "")).strip())

            if not re.fullmatch(r"\d{8}", uid):
                errors.append(f"{uid or '(blank UID)'}: UID must be exactly 8 digits.")
            elif not name:
                errors.append(f"{uid}: player name is blank.")
            elif uid not in known:
                rows_to_save.append([
                    name,
                    uid,  # Google Sheets receives UID as text in this column.
                    USER_EMAIL,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ])
                known.add(uid)

        if errors:
            st.error("Fix these entries before saving:\n\n" + "\n".join(errors))
        elif not rows_to_save:
            st.warning("There are no new approved entries to save.")
        else:
            try:
                sheet.append_rows(rows_to_save, value_input_option="RAW")
                st.success(f"Saved {len(rows_to_save)} new entries to Google Sheets.")
                st.session_state.review_data = None
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save to Google Sheets: {exc}")

st.divider()
st.caption(
    "Uploads are processed in app memory by this app; it does not intentionally save them as files. "
    "OCR may misread decorative names or digits, so review every entry before saving."
)
