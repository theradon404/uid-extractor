import hashlib
import io
import json
import re
import zipfile
from collections import defaultdict

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


def extract_image(image, source):
    text = pytesseract.image_to_string(preprocess_image(image), config="--psm 6")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    results, seen = [], set()

    for index, line in enumerate(lines):
        for uid in UID_RE.findall(line):
            # One occurrence per UID per image/page; cross-image repeats are reported separately.
            if uid in seen:
                continue
            seen.add(uid)
            player_name = ""
            for neighbor in (index - 1, index + 1, index - 2, index + 2):
                if 0 <= neighbor < len(lines) and looks_like_name(lines[neighbor]):
                    player_name = re.sub(r"\s+", " ", lines[neighbor]).strip()
                    break
            results.append({"Name": player_name, "UID": uid, "Source": source})
    return results


def process_upload(upload):
    """Return UID detections, per-page OCR statuses, and downloadable artifacts for problematic pages."""
    data = upload.getvalue()
    results, report, issue_files = [], [], {}

    if upload.name.lower().endswith(".pdf"):
        try:
            document = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:
            report.append({
                "File / page": upload.name,
                "Result": f"Could not open PDF: {exc}",
                "UIDs found": "",
            })
            issue_files[upload.name] = (upload.name, data)
            return results, report, issue_files

        if document.page_count > 40:
            report.append({
                "File / page": upload.name,
                "Result": "Not processed: PDF has more than 40 pages. Split into smaller PDFs.",
                "UIDs found": "",
            })
            issue_files[upload.name] = (upload.name, data)
            return results, report, issue_files

        base_name = upload.name.rsplit(".", 1)[0]
        for page_index, page in enumerate(document):
            source = f"{upload.name} — page {page_index + 1}"
            page_png = None
            try:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2.3, 2.3), alpha=False)
                page_png = pixmap.tobytes("png")
                image = Image.open(io.BytesIO(page_png))
                page_rows = extract_image(image, source)
                results.extend(page_rows)
                found = ", ".join(row["UID"] for row in page_rows)
                report.append({
                    "File / page": source,
                    "Result": f"Found {len(page_rows)} UID(s)" if page_rows else "No 8-digit UID detected",
                    "UIDs found": found,
                })
                if not page_rows:
                    issue_files[source] = (f"{base_name}_page_{page_index + 1:03d}_no_uid.png", page_png)
            except Exception as exc:
                report.append({
                    "File / page": source,
                    "Result": f"OCR error: {exc}",
                    "UIDs found": "",
                })
                if page_png:
                    issue_files[source] = (f"{base_name}_page_{page_index + 1:03d}_ocr_error.png", page_png)
                else:
                    # If the page cannot be rendered, preserve the original PDF for inspection.
                    issue_files[source] = (upload.name, data)
    else:
        source = upload.name
        try:
            image = Image.open(io.BytesIO(data))
            rows = extract_image(image, source)
            results.extend(rows)
            report.append({
                "File / page": source,
                "Result": f"Found {len(rows)} UID(s)" if rows else "No 8-digit UID detected",
                "UIDs found": ", ".join(row["UID"] for row in rows),
            })
            if not rows:
                issue_files[source] = (upload.name, data)
        except Exception as exc:
            report.append({
                "File / page": source,
                "Result": f"Could not read/OCR image: {exc}",
                "UIDs found": "",
            })
            issue_files[source] = (upload.name, data)

    return results, report, issue_files


def build_issue_zip(issue_rows, issue_files):
    """Create a ZIP containing only the images/files listed in the no-UID or OCR-error report."""
    buffer = io.BytesIO()
    written = 0
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, row in enumerate(issue_rows, start=1):
            source = row.get("File / page", "")
            artifact = issue_files.get(source)
            if not artifact:
                continue
            filename, file_bytes = artifact
            safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
            if not safe_filename:
                safe_filename = f"ocr_issue_{index}.bin"
            archive.writestr(f"{index:03d}_{safe_filename}", file_bytes)
            written += 1
    if written == 0:
        return None
    buffer.seek(0)
    return buffer.getvalue()


st.subheader("1. Upload screenshots or PDFs")
uploads = st.file_uploader(
    "Choose one or more files",
    type=["png", "jpg", "jpeg", "webp", "pdf"],
    accept_multiple_files=True,
    help="OCR starts automatically after you choose files. PDF files are processed page by page.",
    max_upload_size=15,
)

for key, initial in (
    ("processed_signature", None),
    ("review_data", None),
    ("submit_notice", None),
    ("ocr_file_report", None),
    ("duplicate_report", None),
    ("uid_sources", None),
    ("ocr_issue_files", None),
):
    if key not in st.session_state:
        st.session_state[key] = initial

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
    st.session_state.ocr_file_report = None
    st.session_state.duplicate_report = None
    st.session_state.uid_sources = None
    st.session_state.ocr_issue_files = None

# OCR automatically when the selected upload set changes.
if uploads and current_signature != st.session_state.processed_signature:
    all_detections, file_report, issue_files = [], [], {}
    with st.spinner("Reading images and PDF pages..."):
        for upload in uploads:
            detections, per_file_report, per_file_issues = process_upload(upload)
            all_detections.extend(detections)
            file_report.extend(per_file_report)
            issue_files.update(per_file_issues)

    # Track all source locations before deduplicating rows for the review table.
    sources_by_uid = defaultdict(list)
    first_row_by_uid = {}
    for row in all_detections:
        uid = row["UID"]
        sources_by_uid[uid].append(row["Source"])
        if uid not in first_row_by_uid:
            first_row_by_uid[uid] = {"Name": row["Name"], "UID": uid}

    duplicate_report = []
    duplicate_occurrences = 0
    for uid, sources in sources_by_uid.items():
        if len(sources) > 1:
            duplicate_occurrences += len(sources) - 1
            duplicate_report.append({
                "UID": uid,
                "First found in": sources[0],
                "Duplicate found in": ", ".join(sources[1:]),
                "Occurrences": len(sources),
            })

    st.session_state.review_data = pd.DataFrame(
        list(first_row_by_uid.values()), columns=["Name", "UID"]
    )
    st.session_state.ocr_file_report = file_report
    st.session_state.ocr_issue_files = issue_files
    st.session_state.duplicate_report = {
        "items": duplicate_report,
        "duplicate_occurrences": duplicate_occurrences,
        "unique_repeated_uids": len(duplicate_report),
        "uid_count": len(first_row_by_uid),
        "detection_count": len(all_detections),
    }
    st.session_state.uid_sources = {uid: list(dict.fromkeys(sources)) for uid, sources in sources_by_uid.items()}
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

if uploads and st.session_state.ocr_file_report is not None:
    st.subheader("2. Upload and OCR report")
    report_data = st.session_state.ocr_file_report
    duplicate_data = st.session_state.duplicate_report or {}
    no_uid_rows = [
        row for row in report_data
        if row["Result"] == "No 8-digit UID detected"
        or row["Result"].startswith("OCR error")
        or row["Result"].startswith("Could not")
        or row["Result"].startswith("Not processed")
    ]

    metric1, metric2, metric3, metric4 = st.columns(4)
    metric1.metric("Files/pages checked", len(report_data))
    metric2.metric("Unique UIDs found", duplicate_data.get("uid_count", 0))
    metric3.metric("Duplicate occurrences", duplicate_data.get("duplicate_occurrences", 0))
    metric4.metric("Pages with no UID / error", len(no_uid_rows))

    st.dataframe(pd.DataFrame(report_data), use_container_width=True, hide_index=True)

    if duplicate_data.get("items"):
        st.warning(
            f"Found {duplicate_data['duplicate_occurrences']} duplicate occurrence(s) "
            f"across {duplicate_data['unique_repeated_uids']} repeated UID value(s)."
        )
        with st.expander("See which UID and picture/page was duplicated", expanded=True):
            st.dataframe(pd.DataFrame(duplicate_data["items"]), use_container_width=True, hide_index=True)
    else:
        st.success("No duplicate UIDs were found across the uploaded images/PDF pages.")

    if no_uid_rows:
        with st.expander("See files/pages where OCR found no UID or failed", expanded=True):
            st.dataframe(pd.DataFrame(no_uid_rows), use_container_width=True, hide_index=True)

        issue_zip = build_issue_zip(no_uid_rows, st.session_state.ocr_issue_files or {})
        if issue_zip:
            st.download_button(
                "Download these files/pages as ZIP",
                data=issue_zip,
                file_name="uid_ocr_issues.zip",
                mime="application/zip",
                help="Contains only the files or individual PDF pages listed in the no-UID/OCR-error table.",
                key="download_ocr_issues_zip",
            )
        else:
            st.info("No downloadable source files were retained for these report rows.")

if uploads and st.session_state.review_data is not None:
    st.subheader("3. Review all unique extracted entries")
    if st.session_state.review_data.empty:
        st.warning("No 8-digit UIDs were detected in the uploaded files. Check the OCR report above.")
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
        already_in_sheet = [uid for uid in review["UID"] if uid in known_uids]
        st.write(
            f"Found **{len(review)}** unique UID(s). "
            f"Already in the database: **{len(set(already_in_sheet))}**."
        )
        if already_in_sheet:
            with st.expander("See UIDs already in the database (will be skipped)"):
                existing_details = []
                for uid in sorted(set(already_in_sheet)):
                    existing_details.append({
                        "UID": uid,
                        "File / page": ", ".join((st.session_state.uid_sources or {}).get(uid, [])),
                    })
                st.dataframe(pd.DataFrame(existing_details), use_container_width=True, hide_index=True)

        st.caption(
            "All new, valid entries in this table will be submitted together. "
            "You can correct names or UIDs before submitting."
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
            # Re-read the sheet immediately before saving.
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
                    rows_to_save.append([name, uid])
                    batch_seen.add(uid)

            if errors:
                st.error("Fix these entries before submitting:\n\n" + "\n".join(errors))
            elif not rows_to_save:
                st.warning("There are no new UIDs to submit. Existing UIDs are skipped.")
            else:
                try:
                    # Find the last occupied row in A:B and write to an explicit range.
                    sheet_values = worksheet.get("A:B")
                    last_data_row = 1
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
                    saved_values = worksheet.get(write_range)
                    expected_values = [[str(name), str(uid)] for name, uid in rows_to_save]
                    actual_values = [
                        [str(row[0]) if len(row) > 0 else "", str(row[1]) if len(row) > 1 else ""]
                        for row in saved_values
                    ]
                    if actual_values != expected_values:
                        raise RuntimeError(
                            f"Write sent to {worksheet.title}!{write_range}, but read-back verification did not match."
                        )

                    sheet_url = (
                        "https://docs.google.com/spreadsheets/d/"
                        + str(st.secrets["spreadsheet_id"]) + "/edit"
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
    "The Google Sheet stores only Name and UID. The upload report shows duplicate UIDs and files/pages "
    "where no 8-digit UID was detected or OCR failed. Review OCR results carefully."
)
