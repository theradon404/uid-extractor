import hashlib
import io
import json
import re

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
    "server", "guild", "rank", "power", "close",
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
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=2)
        worksheet.append_row(["Name", "UID"])

    values = worksheet.get_all_values()
    if not values:
        worksheet.append_row(["Name", "UID"])
    elif values[0][:2] != ["Name", "UID"]:
        raise ValueError(
            f'The first two cells in "{worksheet_name}" must be Name and UID. '
            "Please set A1 to Name and B1 to UID."
        )
    return worksheet


def get_existing_uids(worksheet):
    existing = set()
    for row in worksheet.get_all_values()[1:]:
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
    return ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.7)


def looks_like_name(line):
    cleaned = re.sub(r"\s+", " ", line).strip(" |:：#-—")
    low = cleaned.lower()
    if not 2 <= len(cleaned) <= 36 or low in IGNORED_NAMES or UID_RE.search(cleaned):
        return False
    if re.search(r"\b(level|server|power|rank|uid|id)\b", low):
        return False
    return sum(char.isdigit() for char in cleaned) <= 3


def extract_image(image):
    text = pytesseract.image_to_string(preprocess_image(image), config="--psm 6")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    results, seen = [], set()

    for index, line in enumerate(lines):
        for uid in UID_RE.findall(line):
            if uid in seen:
                continue
            seen.add(uid)
            player_name = ""
            for neighbor in (index - 1, index + 1, index - 2, index + 2):
                if 0 <= neighbor < len(lines) and looks_like_name(lines[neighbor]):
                    player_name = re.sub(r"\s+", " ", lines[neighbor]).strip()
                    break
            results.append({"Name": player_name, "UID": uid})
    return results


def process_upload(upload):
    data = upload.getvalue()
    results = []
    if upload.name.lower().endswith(".pdf"):
        document = fitz.open(stream=data, filetype="pdf")
        if document.page_count > 40:
            raise ValueError("PDF has more than 40 pages. Please split it into smaller files.")
        for page in document:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.3, 2.3), alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            results.extend(extract_image(image))
    else:
        results.extend(extract_image(Image.open(io.BytesIO(data))))
    return results


st.subheader("1. Upload screenshots or PDFs")
uploads = st.file_uploader(
    "Choose one or more files",
    type=["png", "jpg", "jpeg", "webp", "pdf"],
    accept_multiple_files=True,
    help="OCR starts automatically after you choose files. PDF files are processed page by page.",
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

# Run OCR once when the selected files change.
if uploads and current_signature != st.session_state.processed_signature:
    extracted = []
    with st.spinner("Reading images and PDF pages..."):
        for upload in uploads:
            try:
                extracted.extend(process_upload(upload))
            except Exception as exc:
                st.warning(f"Could not process {upload.name}: {exc}")

    # Deduplicate UID values across all uploaded files, preserving first occurrence.
    unique_rows, seen = [], set()
    for row in extracted:
        if row["UID"] not in seen:
            seen.add(row["UID"])
            unique_rows.append(row)

    st.session_state.review_data = pd.DataFrame(unique_rows, columns=["Name", "UID"])
    st.session_state.processed_signature = current_signature
    st.session_state.submit_notice = None

if st.session_state.submit_notice:
    kind, message = st.session_state.submit_notice
    if kind == "success":
        st.success(message)
        if st.session_state.get("saved_sheet_url"):
            st.markdown(f"[Open the Google Sheet]({st.session_state.saved_sheet_url})")
    else:
        st.warning(message)

if uploads and st.session_state.review_data is not None:
    st.subheader("2. Review all extracted entries")
    if st.session_state.review_data.empty:
        st.warning("No 8-digit UIDs were detected. Try a clearer screenshot or crop the profile area.")
    else:
        try:
            worksheet = get_worksheet()
            known_uids = get_existing_uids(worksheet)
        except Exception as exc:
            worksheet = None
            known_uids = set()
            st.error(
                "Could not connect to the UID database. Check spreadsheet_id and "
                "gcp_service_account_json in Streamlit Secrets. Details: " + str(exc)
            )

        review = st.session_state.review_data[["Name", "UID"]].copy()
        review["Name"] = review["Name"].astype(str).str.strip()
        review["UID"] = review["UID"].astype(str).str.strip()
        already_count = sum(uid in known_uids for uid in review["UID"])

        st.write(
            f"Found **{len(review)}** unique UID(s). "
            f"Already in the database: **{already_count}**."
        )
        st.caption(
            "All new, valid entries in this table will be submitted together. "
            "You can correct a name or UID before submitting."
        )

        edited = st.data_editor(
            review,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            column_config={
                "Name": st.column_config.TextColumn("Name"),
                "UID": st.column_config.TextColumn("UID", help="Must be exactly 8 digits."),
            },
            key="uid_review_editor",
        )

        st.download_button(
            "Download extracted entries as CSV",
            data=edited[["Name", "UID"]].to_csv(index=False).encode("utf-8-sig"),
            file_name="uid_extraction.csv",
            mime="text/csv",
        )

        if st.button("Submit all new UIDs to database", type="primary", disabled=worksheet is None):
            rows_to_save, errors = [], []
            # Re-read the database immediately before writing to reduce duplicate races.
            known_uids = get_existing_uids(worksheet)
            batch_seen = set()

            for _, row in edited.iterrows():
                name = str(row.get("Name", "")).strip()
                uid = re.sub(r"\s+", "", str(row.get("UID", "")).strip())

                if not re.fullmatch(r"\d{8}", uid):
                    errors.append(f"{uid or '(blank UID)'}: UID must be exactly 8 digits.")
                elif not name:
                    errors.append(f"{uid}: name is blank.")
                elif uid in known_uids or uid in batch_seen:
                    continue
                else:
                    # Write exactly two values per row: Name and UID.
                    rows_to_save.append([name, uid])
                    batch_seen.add(uid)

            if errors:
                st.error("Fix these entries before submitting:\n\n" + "\n".join(errors))
            elif not rows_to_save:
                st.warning("There are no new UIDs to submit. Existing UIDs are skipped.")
            else:
                try:
                    # Avoid append-table detection guessing the wrong location:
                    # find the last non-empty row in columns A:B, then write to an explicit range.
                    sheet_values = worksheet.get("A:B")
                    last_data_row = 1  # row 1 contains the Name / UID headers
                    for row_number, values in enumerate(sheet_values, start=1):
                        if any(str(cell).strip() for cell in values[:2]):
                            last_data_row = row_number

                    first_row = last_data_row + 1
                    last_row = first_row + len(rows_to_save) - 1
                    write_range = f"A{first_row}:B{last_row}"

                    worksheet.update(
                        range_name=write_range,
                        values=rows_to_save,
                        value_input_option="RAW",
                    )

                    # Verify the exact cells we just wrote before reporting success.
                    saved_values = worksheet.get(write_range)
                    expected_values = [[str(name), str(uid)] for name, uid in rows_to_save]
                    actual_values = [
                        [str(row[0]) if len(row) > 0 else "",
                         str(row[1]) if len(row) > 1 else ""]
                        for row in saved_values
                    ]
                    if actual_values != expected_values:
                        raise RuntimeError(
                            f"Write was sent to {worksheet.title}!{write_range}, "
                            "but read-back verification did not match."
                        )

                    sheet_url = (
                        "https://docs.google.com/spreadsheets/d/"
                        + str(st.secrets["spreadsheet_id"])
                        + "/edit"
                    )
                    st.session_state.submit_notice = (
                        "success",
                        f"Verified {len(rows_to_save)} UID(s) in worksheet "
                        f"“{worksheet.title}”, cells {write_range}.",
                    )
                    st.session_state.saved_sheet_url = sheet_url
                    st.session_state.review_data = None
                    st.session_state.processed_signature = current_signature
                    st.rerun()
                except Exception as exc:
                    st.error(
                        "Could not verify the saved rows in the configured Google Sheet. "
                        "No success message was shown. Check the spreadsheet ID, the UIDs tab, "
                        f"and the service account's Editor access. Details: {exc}"
                    )

st.divider()
st.caption(
    "Public website: all new, valid, non-duplicate entries are submitted together. "
    "Only Name and UID are written to the database. OCR may misread names or digits, so review the table before submitting."
)
