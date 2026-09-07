from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import gspread
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from telethon import TelegramClient
from telethon.sessions import StringSession


HEADER_ROW = 1
COL_TELEGRAM_LINK = "Telegram_video_link"
COL_PREVIEW_IMAGE = "Preview"
SHEET_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
PRIVATE_LINK = re.compile(r"^/c/(?P<channel>\d+)/(?P<message>\d+)/?$")
PUBLIC_LINK = re.compile(r"^/(?P<username>[A-Za-z][A-Za-z0-9_]{3,})/(?P<message>\d+)/?$")


def normalize_header(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise EnvironmentError(f"Thiếu biến môi trường: {name}")
    return value


def google_credentials() -> ServiceAccountCredentials:
    credentials_json = required_env("GOOGLE_CREDENTIALS")
    try:
        info = json.loads(credentials_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GOOGLE_CREDENTIALS không phải JSON hợp lệ: {exc}") from exc
    return ServiceAccountCredentials.from_service_account_info(info, scopes=SHEET_SCOPES)


def google_drive_credentials() -> UserCredentials:
    oauth_json = required_env("OAuth_Google")
    try:
        info = json.loads(oauth_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OAuth_Google không phải JSON hợp lệ: {exc}") from exc

    required_fields = ("client_id", "client_secret", "refresh_token")
    missing = [field for field in required_fields if not str(info.get(field, "")).strip()]
    if missing:
        raise ValueError("OAuth_Google thiếu trường bắt buộc: " + ", ".join(missing))

    return UserCredentials(
        token=None,
        refresh_token=str(info["refresh_token"]).strip(),
        token_uri=str(info.get("token_uri") or "https://oauth2.googleapis.com/token").strip(),
        client_id=str(info["client_id"]).strip(),
        client_secret=str(info["client_secret"]).strip(),
        scopes=DRIVE_SCOPES,
    )


def worksheet(creds: ServiceAccountCredentials | None = None):
    sheet_id = required_env("GOOGLE_SHEET_ID")
    tab_name = os.getenv("GOOGLE_SHEET_TAB", "Kho link video tele")
    creds = creds or google_credentials()
    return gspread.authorize(creds).open_by_key(sheet_id).worksheet(tab_name)


def parse_message_link(link: str) -> tuple[int | str, int]:
    parsed = urlparse((link or "").strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "t.me", "www.t.me", "telegram.me"
    }:
        raise ValueError("Link Telegram không hợp lệ")

    private_match = PRIVATE_LINK.match(parsed.path)
    if private_match:
        channel_id = private_match.group("channel")
        return int(f"-100{channel_id}"), int(private_match.group("message"))

    public_match = PUBLIC_LINK.match(parsed.path)
    if public_match:
        return public_match.group("username"), int(public_match.group("message"))

    raise ValueError(
        "Chỉ hỗ trợ https://t.me/c/CHANNEL_ID/MESSAGE_ID hoặc https://t.me/USERNAME/MESSAGE_ID"
    )


def image_filename(link: str) -> str:
    parsed = urlparse((link or "").strip())
    private_match = PRIVATE_LINK.match(parsed.path)
    if private_match:
        return f"{private_match.group('channel')}_{private_match.group('message')}.jpg"

    public_match = PUBLIC_LINK.match(parsed.path)
    if public_match:
        return f"{public_match.group('username')}_{public_match.group('message')}.jpg"

    raise ValueError("Không thể tạo tên ảnh từ link Telegram")


def drive_image_url(file_id: str) -> str:
    return (
        "https://drive.usercontent.google.com/download"
        f"?id={file_id}&export=view&authuser=0"
    )


def get_sheet_layout(ws) -> tuple[int, int]:
    headers = ws.row_values(HEADER_ROW)
    header_map = {
        normalize_header(value): index + 1
        for index, value in enumerate(headers)
        if normalize_header(value)
    }
    link_col = header_map.get(normalize_header(COL_TELEGRAM_LINK))
    preview_col = header_map.get(normalize_header(COL_PREVIEW_IMAGE))
    missing = []
    if not link_col:
        missing.append(COL_TELEGRAM_LINK)
    if not preview_col:
        missing.append(COL_PREVIEW_IMAGE)
    if missing:
        raise ValueError(f"Sheet thiếu cột bắt buộc: {', '.join(missing)}")
    return link_col, preview_col


def preview_request(row_number: int, preview_col: int, file_id: str) -> dict[str, object]:
    url = drive_image_url(file_id)
    formula = f'=IMAGE("{url}")'
    return {
        "range": gspread.utils.rowcol_to_a1(row_number, preview_col),
        "values": [[formula]],
    }


def batch_write_previews(ws, requests: list[dict[str, object]]) -> None:
    if not requests:
        return
    for attempt in range(6):
        try:
            ws.batch_update(requests, value_input_option="USER_ENTERED")
            return
        except gspread.exceptions.APIError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status != 429 or attempt == 5:
                raise
            wait_seconds = min(60, 5 * (2 ** attempt))
            print(f"Sheets quota 429: chờ {wait_seconds}s rồi thử lại...")
            time.sleep(wait_seconds)


def write_preview(ws, row_number: int, preview_col: int, file_id: str) -> None:
    batch_write_previews(ws, [preview_request(row_number, preview_col, file_id)])


def find_drive_file(drive, folder_id: str, filename: str) -> str | None:
    safe_name = filename.replace("'", "\\'")
    query = (
        f"name = '{safe_name}' and '{folder_id}' in parents "
        "and trashed = false"
    )
    result = drive.files().list(
        q=query,
        spaces="drive",
        fields="files(id,name)",
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = result.get("files", [])
    return files[0]["id"] if files else None


def ensure_public_reader(drive, file_id: str) -> None:
    permissions = drive.permissions().list(
        fileId=file_id,
        fields="permissions(id,type,role)",
        supportsAllDrives=True,
    ).execute().get("permissions", [])
    if any(p.get("type") == "anyone" and p.get("role") == "reader" for p in permissions):
        return
    drive.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
        fields="id",
        supportsAllDrives=True,
    ).execute()


def upload_to_drive(drive, folder_id: str, path: Path) -> str:
    existing_id = find_drive_file(drive, folder_id, path.name)
    if existing_id:
        return existing_id

    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    media = MediaFileUpload(str(path), mimetype=mime_type, resumable=False)
    created = drive.files().create(
        body={"name": path.name, "parents": [folder_id]},
        media_body=media,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    file_id = created["id"]
    ensure_public_reader(drive, file_id)
    return file_id


async def download_previews(limit: int | None, recover_only: bool) -> None:
    sheet_creds = google_credentials()
    ws = worksheet(sheet_creds)
    values = ws.get_all_values()
    if not values:
        raise ValueError("Tab Kho link video tele đang trống")

    link_col, preview_col = get_sheet_layout(ws)
    pending: list[tuple[int, str]] = []
    for row_number, row in enumerate(values[HEADER_ROW:], start=HEADER_ROW + 1):
        link = row[link_col - 1].strip() if link_col <= len(row) else ""
        preview = row[preview_col - 1].strip() if preview_col <= len(row) else ""
        if not link or preview:
            continue
        pending.append((row_number, link))
        if limit is not None and len(pending) >= limit:
            break

    if not pending:
        print("Không có dòng nào cần lấy Preview.")
        return

    drive_folder_id = required_env("GOOGLE_DRIVE_PREVIEW_FOLDER_ID")
    drive_creds = google_drive_credentials()
    drive = build("drive", "v3", credentials=drive_creds, cache_discovery=False)

    missing_on_drive: list[tuple[int, str, str]] = []
    recover_requests: list[dict[str, object]] = []
    recovered_rows: list[tuple[int, str]] = []

    for row_number, link in pending:
        try:
            filename = image_filename(link)
            existing_id = find_drive_file(drive, drive_folder_id, filename)
            if existing_id:
                recover_requests.append(preview_request(row_number, preview_col, existing_id))
                recovered_rows.append((row_number, filename))
            else:
                missing_on_drive.append((row_number, link, filename))
        except Exception as exc:
            print(f"LỖI recover dòng {row_number}: {exc}")

    recovered = 0
    if recover_requests:
        # Một batch_update có thể ghi hàng trăm ô nhưng chỉ tính là một write request.
        # Chia 500 dòng/batch để payload vẫn gọn nếu sheet rất lớn.
        for start in range(0, len(recover_requests), 500):
            chunk = recover_requests[start:start + 500]
            batch_write_previews(ws, chunk)
            recovered += len(chunk)
            print(f"RECOVER: đã ghi {recovered}/{len(recover_requests)} Preview từ Drive")

    print(f"Đã khôi phục {recovered} Preview từ ảnh đã có trên Google Drive.")
    if recover_only:
        print(f"Recover-only: còn {len(missing_on_drive)} dòng chưa có ảnh trên Drive, không tải mới.")
        return
    if not missing_on_drive:
        print("Tất cả dòng cần xử lý đã có ảnh trên Drive và đã được ghi vào Sheet.")
        return

    api_id_raw = required_env("TELEGRAM_API_ID")
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID phải là số nguyên") from exc

    api_hash = required_env("TELEGRAM_API_HASH")
    session = required_env("TELEGRAM_SESSION")
    temp_dir = Path("preview_temp")
    temp_dir.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    uploaded = 0
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("TELEGRAM_SESSION hết hạn hoặc chưa đăng nhập")

        entity_cache: dict[int | str, object] = {}
        for row_number, link, filename in missing_on_drive:
            destination: Path | None = None
            try:
                entity_ref, message_id = parse_message_link(link)
                entity = entity_cache.get(entity_ref)
                if entity is None:
                    entity = await client.get_input_entity(entity_ref)
                    entity_cache[entity_ref] = entity

                message = await client.get_messages(entity, ids=message_id)
                if not message or not message.media:
                    raise ValueError("Không tìm thấy tin nhắn hoặc tin nhắn không có media")

                destination = temp_dir / filename
                downloaded = await client.download_media(
                    message,
                    file=str(destination),
                    thumb=-1,
                )
                if not downloaded:
                    raise RuntimeError("Telegram không có thumbnail cho media này")

                downloaded_path = Path(downloaded)
                if not downloaded_path.is_file() or downloaded_path.stat().st_size == 0:
                    raise RuntimeError("Thumbnail tải về bị trống")

                if downloaded_path != destination:
                    if destination.exists():
                        destination.unlink()
                    downloaded_path.replace(destination)

                file_id = upload_to_drive(drive, drive_folder_id, destination)
                write_preview(ws, row_number, preview_col, file_id)
                uploaded += 1
                print(f"OK dòng {row_number}: {filename} -> Drive {file_id} -> đã ghi Preview")
            except Exception as exc:
                print(f"LỖI dòng {row_number}: {exc}")
            finally:
                if destination and destination.exists():
                    destination.unlink()
    finally:
        await client.disconnect()

    print(
        f"Hoàn tất: khôi phục {recovered} ảnh có sẵn trên Drive, "
        f"tải mới và ghi ngay {uploaded} ảnh vào Sheet."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lấy thumbnail Telegram, upload Drive và ghi Preview ngay vào Google Sheet"
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--recover-only",
        action="store_true",
        help="Chỉ tìm ảnh đã có trên Drive và ghi Preview vào Sheet, không tải ảnh mới từ Telegram",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit phải lớn hơn 0")
    asyncio.run(download_previews(args.limit, args.recover_only))


if __name__ == "__main__":
    main()
