import hashlib
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
st.caption("Upload game profile screenshots or PDFs. The app extracts names and 8-digit UIDs automatically.")

UID_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")
IGNORED_NAMES = {
    "uid", "id", "level", "lvl", "profile", "player profile", "copy",
    "server", "guild", "rank", "power", "close"
}
SHEET_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


@st.cache_resource
def get_sheets_client():
    credentials = Credentials.from_service_account_info(
        json.loads(st.secrets["gcp_service_account_json"]),
        scopes=SHEET_SCOPES,
    )
    return gspread.authorize(credentials)


def get_worksheet():
    """Main UID database. Public users may append validated entries here."""
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
            "Expected headers: Name, UID, SubmittedBy, SubmittedAt."
        )
    return worksheet


def existing_uids(worksheet):
    uids = set()
    for row in worksheet.get_all_values()[1:]:
        if len(row) > 1:
            match = UID_RE.search(str(row[1]))
            if match:
                uids.add(match.group(1))
    return uids


def preprocess_image(image):
    image = ImageOps.exif_transpose(image).convert("RGB")
    if image.width < 1200:
        scale = min(3, 1200 / image.width)
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    return ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.7)


def looks_like_name(line):
    cleaned = re.sub(r"\s+", " ", line).strip(" |:：#-—")
    low = cleaned.lower()
    if not 2 <= len(cleaned) <= 36 or low in IGNORED_NAMES or UID_RE.search(cleaned):
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
            player_name = ""
            # Guess the username from the nearest plausible OCR line.
            for neighbor in (index - 1, index + 1, index - 2, index + 2):
                if 0 <= neighbor < len(lines) and looks_like_name(lines[neighbor]):
                    player_name = re.sub(r"\s+", " ", lines[neighbor]).strip()
                    break
            results.append({
                "Name": player_name,
                "UID": uid,
                "Source": source,
                "Submit": True,
            })
    return results


def process_upload(upload):
    file_data = upload.getvalue()
    results = []
    if upload.name.lower().endswith(".pdf"):
        document = fitz.open(stream=file_data, filetype="pdf")
        if document.page_count > 40:
            raise ValueError("PDF has more than 40 pages. Please split it into smaller files.")
        for page_index, page in enumerate(document):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.3, 2.3), alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            results.extend(extract_image(image, f"{upload.name} — page {page_index + 1}"))
    else:
        image = Image.open(io.BytesIO(file_data))
        results.extend(extract_image(image, upload.name))
    return results


st.subheader("1. Upload screenshots or PDFs")
uploads = st.file_uploader(
    "Choose one or more files",
    type=["png", "jpg", "jpeg", "webp", "pdf"],
    accept_multiple_files=True,
    help="Extraction starts automatically after you choose files. PDF files are processed page by page.",
    max_upload_size=15,
)

if "processed_signature" not in st.session_state:
    st.session_state.processed_signature = None
if "review_data" not in st.session_state:
    st.session_state.review_data = None
if "submit_notice" not in st.session_state:
    st.session_state.submit_notice = None

current_signature = None
if uploads:
    current_signature = tuple(
        (upload.name, hashlib.sha256(upload.getvalue()).hexdigest())
        for upload in uploads
    )
elif st.session_state.processed_signature is not None:
    st.session_state.processed_signature = None
    st.session_state.review_data = None
    st.session_state.submit_notice = None

# OCR runs automatically once whenever the selected file set changes.
if uploads and current_signature != st.session_state.processed_signature:
    extracted = []
    with st.spinner("Reading images and PDF pages..."):
        for upload in uploads:
            try:
                extracted.extend(process_upload(upload))
            except Exception as exc:
                st.warning(f"Could not process {upload.name}: {exc}")

    unique_rows, seen_batch = [], set()
    for row in extracted:
        if row["UID"] not in seen_batch:
            seen_batch.add(row["UID"])
            unique_rows.append(row)

    st.session_state.review_data = pd.DataFrame(
        unique_rows, columns=["Name", "UID", "Source", "Submit"]
    )
    st.session_state.processed_signature = current_signature
    st.session_state.submit_notice = None

if st.session_state.submit_notice:
    kind, message = st.session_state.submit_notice
    if kind == "success":
        st.success(message)
    else:
        st.warning(message)

if uploads and st.session_state.review_data is not None:
    st.subheader("2. Select the UIDs to submit")
    if st.session_state.review_data.empty:
        st.warning("No 8-digit UIDs were detected. Try a clearer screenshot or crop the profile area.")
    else:
        try:
            worksheet = get_worksheet()
            known_uids = existing_uids(worksheet)
            sheet_error = None
        except Exception as exc:
            worksheet = None
            known_uids = set()
            sheet_error = str(exc)
            st.error(
                "Could not connect to the main UID database. Check the Streamlit Secrets "
                "for the spreadsheet ID and service-account JSON."
            )

        review = st.session_state.review_data.copy()
        review["UID"] = review["UID"].astype(str).str.strip()
        review["Already in database"] = review["UID"].isin(known_uids)
        review["Repeated in upload"] = review["UID"].duplicated(keep="first")
        review.loc[
            review["Already in database"] | review["Repeated in upload"], "Submit"
        ] = False

        st.write(
            f"Found **{len(review)}** unique UID(s). "
            f"Already in the database: **{int(review['Already in database'].sum())}**."
        )
        st.caption(
            "Correct any names or UIDs, then tick Submit for the entries you want to add. "
            "Only selected, valid, new UIDs will be saved."
        )

        edited = st.data_editor(
            review,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "Name": st.column_config.TextColumn("Player name"),
                "UID": st.column_config.TextColumn("8-digit UID", help="Must be exactly 8 digits."),
                "Source": st.column_config.TextColumn("Source file/page", disabled=True),
                "Submit": st.column_config.CheckboxColumn("Submit"),
                "Already in database": st.column_config.CheckboxColumn("Already in database", disabled=True),
                "Repeated in upload": st.column_config.CheckboxColumn("Repeated in upload", disabled=True),
            },
            disabled=["Source", "Already in database", "Repeated in upload"],
            key="uid_review_editor",
        )

        st.download_button(
            "Download reviewed entries as CSV",
            data=edited[["Name", "UID"]].to_csv(index=False).encode("utf-8-sig"),
            file_name="uid_extraction.csv",
            mime="text/csv",
        )

        if st.button(
            "Submit selected UIDs to database",
            type="primary",
            disabled=worksheet is None,
        ):
            selected, errors = [], []
            for _, row in edited.iterrows():
                if not bool(row.get("Submit", False)):
                    continue
                name = str(row.get("Name", "")).strip()
                uid = re.sub(r"\s+", "", str(row.get("UID", "")).strip())
                if not re.fullmatch(r"\d{8}", uid):
                    errors.append(f"{uid or '(blank UID)'}: UID must be exactly 8 digits.")
                elif not name:
                    errors.append(f"{uid}: player name is blank.")
                elif uid in known_uids:
                    continue
                else:
                    selected.append([
                        name,
                        uid,
                        "Public submission",
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ])
                    known_uids.add(uid)

            if errors:
                st.error("Fix these entries before submitting:\n\n" + "\n".join(errors))
            elif not selected:
                st.warning("Select at least one new UID to submit. Existing UIDs are skipped.")
            else:
                try:
                    worksheet.append_rows(selected, value_input_option="RAW")
                    st.session_state.submit_notice = (
                        "success",
                        f"Successfully added {len(selected)} UID(s) to the main database.",
                    )
                    st.session_state.review_data = None
                    st.session_state.processed_signature = current_signature
                    st.rerun()
                except Exception as exc:
                    st.error(
                        "Could not save to the main UID database. "
                        "Check Google Sheets access and try again. "
                        f"Details: {exc}"
                    )

st.divider()
st.caption(
    "This is a public submission app. Anyone with access can submit entries directly to the main database. "
    "The app validates 8-digit UIDs and skips UIDs that already exist. Review names carefully because OCR can make mistakes."
)
