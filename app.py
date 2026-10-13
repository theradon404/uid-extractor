import io
import json
import re
import hmac
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
st.caption("Upload game profile screenshots or PDFs, review the extracted names and UIDs, and submit them for approval.")

st.info(
    "This is the public submission page. Entries go into a pending-review queue first; "
    "they are not added to the main UID database until the site owner approves them."
)

UID_RE = re.compile(r"(?<!\\d)(\\d{8})(?!\\d)")
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


def get_spreadsheet():
    return get_sheets_client().open_by_key(st.secrets["spreadsheet_id"])


def get_worksheet():
    """Master UID database. Only the password-protected admin panel writes here."""
    spreadsheet = get_spreadsheet()
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


def get_pending_worksheet():
    """Public visitors can only append to this staging queue."""
    spreadsheet = get_spreadsheet()
    try:
        worksheet = spreadsheet.worksheet("Pending")
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title="Pending", rows=2000, cols=5)
        worksheet.append_row(["Name", "UID", "Source", "SubmittedAt", "Status"])
    values = worksheet.get_all_values()
    if not values:
        worksheet.append_row(["Name", "UID", "Source", "SubmittedAt", "Status"])
    elif values[0][:5] != ["Name", "UID", "Source", "SubmittedAt", "Status"]:
        raise ValueError(
            'The "Pending" worksheet headers must be: Name, UID, Source, SubmittedAt, Status.'
        )
    return worksheet


def extract_existing_uids(worksheet):
    values = worksheet.get_all_values()
    uids = set()
    for row in values[1:]:
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
    cleaned = re.sub(r"\\s+", " ", line).strip(" |:：#-—")
    low = cleaned.lower()
    if not 2 <= len(cleaned) <= 36 or low in IGNORED_NAMES or UID_RE.search(cleaned):
        return False
    if re.search(r"\\b(level|server|power|rank|uid|id)\\b", low):
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
            for neighbor in (index - 1, index + 1, index - 2, index + 2):
                if 0 <= neighbor < len(lines) and looks_like_name(lines[neighbor]):
                    player_name = re.sub(r"\\s+", " ", lines[neighbor]).strip()
                    break
            results.append({"Name": player_name, "UID": uid, "Source": source, "Submit": True})
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


uploads = st.file_uploader(
    "Choose profile images or PDFs",
    type=["png", "jpg", "jpeg", "webp", "pdf"],
    accept_multiple_files=True,
    help="Select multiple screenshots and PDFs. PDF files are processed page by page.",
    max_upload_size=15,
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

    unique_rows, seen_batch = [], set()
    for row in extracted:
        if row["UID"] not in seen_batch:
            seen_batch.add(row["UID"])
            unique_rows.append(row)

    st.session_state.review_data = pd.DataFrame(
        unique_rows, columns=["Name", "UID", "Source", "Submit"]
    )
    if not unique_rows:
        st.warning("No 8-digit UIDs were detected. Try a clearer screenshot or crop the profile area.")

if st.session_state.review_data is not None:
    st.subheader("Review your extracted entries")
    st.caption("Correct any OCR mistakes. Selected rows will be sent to the pending queue for the site owner to review.")
    review = st.session_state.review_data.copy()
    review["Repeated in upload"] = review["UID"].duplicated(keep="first")
    review.loc[review["Repeated in upload"], "Submit"] = False

    edited = st.data_editor(
        review,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_config={
            "Name": st.column_config.TextColumn("Player name"),
            "UID": st.column_config.TextColumn("8-digit UID", help="Must be exactly 8 digits."),
            "Source": st.column_config.TextColumn("Source file/page", disabled=True),
            "Submit": st.column_config.CheckboxColumn("Submit for review"),
            "Repeated in upload": st.column_config.CheckboxColumn("Repeated in upload", disabled=True),
        },
        disabled=["Source", "Repeated in upload"],
        key="uid_review_editor",
    )

    csv_df = edited[["Name", "UID"]].copy()
    st.download_button(
        "Download reviewed entries as CSV",
        data=csv_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="uid_extraction.csv",
        mime="text/csv",
    )

    if st.button("Submit selected entries for approval", type="primary"):
        rows_to_submit, errors = [], []
        for _, row in edited.iterrows():
            if not bool(row.get("Submit", False)):
                continue
            name = str(row.get("Name", "")).strip()
            uid = re.sub(r"\\s+", "", str(row.get("UID", "")).strip())
            source = str(row.get("Source", "public upload")).strip()[:250]
            if not re.fullmatch(r"\\d{8}", uid):
                errors.append(f"{uid or '(blank UID)'}: UID must be exactly 8 digits.")
            elif not name:
                errors.append(f"{uid}: player name is blank.")
            else:
                rows_to_submit.append([
                    name, uid, source,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "Pending",
                ])

        if errors:
            st.error("Fix these entries before submitting:\\n\\n" + "\\n".join(errors))
        elif not rows_to_submit:
            st.warning("No entries were selected.")
        else:
            try:
                pending_sheet = get_pending_worksheet()
                already_pending = extract_existing_uids(pending_sheet)
                filtered, skipped = [], 0
                local_seen = set()
                for row in rows_to_submit:
                    uid = row[1]
                    if uid in already_pending or uid in local_seen:
                        skipped += 1
                        continue
                    filtered.append(row)
                    local_seen.add(uid)
                if filtered:
                    pending_sheet.append_rows(filtered, value_input_option="RAW")
                st.success(
                    f"Submitted {len(filtered)} UID(s) for owner review. "
                    f"Skipped {skipped} UID(s) already pending."
                )
                st.session_state.review_data = None
                st.rerun()
            except Exception as exc:
                st.error(f"Could not submit entries. Please try again later. Details: {exc}")

st.divider()
st.subheader("Site owner: review submissions")
st.caption("This section is only for the site owner. Public visitors can submit entries, but cannot approve them into the main database.")
admin_password = st.text_input("Admin password", type="password", key="uid_admin_password")
if st.button("Open pending submissions"):
    configured_password = str(st.secrets.get("admin_password", ""))
    if not configured_password:
        st.session_state.admin_authenticated = False
        st.error("Admin access is not configured. The owner must add admin_password in Streamlit Secrets.")
    elif hmac.compare_digest(admin_password, configured_password):
        st.session_state.admin_authenticated = True
    else:
        st.session_state.admin_authenticated = False
        st.error("Incorrect admin password.")

if st.session_state.get("admin_authenticated", False):
    try:
        pending_sheet = get_pending_worksheet()
        master_sheet = get_worksheet()
        values = pending_sheet.get_all_values()
        pending_rows = []
        for sheet_row, row in enumerate(values[1:], start=2):
            row = row + [""] * (5 - len(row))
            if row[4] == "Pending":
                pending_rows.append({
                    "Name": row[0], "UID": row[1], "Source": row[2],
                    "SubmittedAt": row[3], "Status": row[4],
                    "Approve": True, "_sheet_row": sheet_row,
                })
        if not pending_rows:
            st.success("There are no pending submissions.")
        else:
            st.write(f"Pending submissions: **{len(pending_rows)}**")
            pending_df = pd.DataFrame(pending_rows)
            edited_pending = st.data_editor(
                pending_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                column_config={
                    "Name": st.column_config.TextColumn("Player name"),
                    "UID": st.column_config.TextColumn("8-digit UID"),
                    "Source": st.column_config.TextColumn("Source file", disabled=True),
                    "SubmittedAt": st.column_config.TextColumn("Submitted at", disabled=True),
                    "Status": st.column_config.TextColumn("Status", disabled=True),
                    "Approve": st.column_config.CheckboxColumn("Approve into main database"),
                    "_sheet_row": None,
                },
                disabled=["Source", "SubmittedAt", "Status", "_sheet_row"],
                key="uid_pending_editor",
            )
            if st.button("Approve selected submissions into main UID database", type="primary"):
                known = extract_existing_uids(master_sheet)
                to_append, statuses, errors = [], [], []
                for _, row in edited_pending.iterrows():
                    sheet_row = int(row["_sheet_row"])
                    uid = re.sub(r"\\s+", "", str(row.get("UID", "")).strip())
                    name = str(row.get("Name", "")).strip()
                    if not bool(row.get("Approve", False)):
                        continue
                    if not re.fullmatch(r"\\d{8}", uid) or not name:
                        errors.append(f"Pending row {sheet_row}: fix the name and 8-digit UID.")
                    elif uid in known:
                        statuses.append((sheet_row, "Duplicate"))
                    else:
                        to_append.append([
                            name, uid, "Public submission",
                            datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        ])
                        known.add(uid)
                        statuses.append((sheet_row, "Approved"))
                if to_append:
                    master_sheet.append_rows(to_append, value_input_option="RAW")
                for sheet_row, status in statuses:
                    pending_sheet.update_cell(sheet_row, 5, status)
                if to_append:
                    st.success(f"Approved and added {len(to_append)} UID(s) to the main database.")
                if statuses and not to_append:
                    st.info("Selected entries were marked as duplicates where appropriate.")
                if errors:
                    st.error("Some entries were not processed:\\n\\n" + "\\n".join(errors))
                st.rerun()
    except Exception as exc:
        st.error(f"Could not load the pending queue. Check Google Sheets Secrets and permissions. Details: {exc}")

st.caption(
    "Privacy: uploaded files are processed in memory and are not intentionally saved as files. "
    "Public submissions enter a pending queue; only the site owner can approve entries into the main database."
)
