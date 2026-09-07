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
LEGACY_PUBLIC_C_LINK = re.compile(r"^/c/(?P<username>[A-Za-z][A-Za-z0-9_]{3,})/(?P<message>\d+)/?$")


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

    legacy_public_match = LEGACY_PUBLIC_C_LINK.match(parsed.path)
    if legacy_public_match:
        return legacy_public_match.group("username"), int(legacy_public_match.group("message"))

    raise ValueError(
        "Chỉ hỗ trợ https://t.me/c/CHANNEL_ID/MESSAGE_ID, https://t.me/USERNAME/MESSAGE_ID hoặc dạng c/USERNAME cũ"
    )


def image_filename(link: str) -> str:
    parsed = urlparse((link or "").strip())

    private_match = PRIVATE_LINK.match(parsed.path)
    if private_match:
        return f"{private_match.group('channel')}_{private_match.group('message')}.jpg"

    public_match = PUBLIC_LINK.match(parsed.path)
    if public_match:
        return f"{public_match.group('username')}_{public_match.group('message')}.jpg"

    legacy_public_match = LEGACY_PUBLIC_C_LINK.match(parsed.path)
    if legacy_public_match:
        return f"{legacy_public_match.group('username')}_{legacy_public_match.group('message')}.jpg"

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


def get_pending_rows(ws, limit: int | None) -> tuple[int, list[tuple[int, str]]]:
    values = ws.get_all_values(value_render_option="FORMULA")
    if not values:
        raise ValueError("Tab Kho link video tele đang trống")

    link_col, preview_col = get_sheet_layout(ws)
    pending: list[tuple[int, str]] = []
    skipped_with_preview = 0
    for row_number, row in enumerate(values[HEADER_ROW:], start=HEADER_ROW + 1):
        link = row[link_col - 1].strip() if link_col <= len(row) else ""
        preview = row[preview_col - 1].strip() if preview_col <= len(row) else ""
        if not link:
            continue
        if preview:
            skipped_with_preview += 1
            continue
        pending.append((row_number, link))
        if limit is not None and len(pending) >= limit:
            break

    print(f"Bỏ qua {skipped_with_preview} dòng đã có Preview.")
    return preview_col, pending


def preview_request(row_number: int, preview_col: int, file_id: str) -> dict[str, object]:
    formula = f'=IMAGE("{drive_image_url(file_id)}")'
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


def build_drive():
    return build(
        "drive",
        "v3",
        credentials=google_drive_credentials(),
        cache_discovery=False,
    )


def recover_previews(limit: int | None) -> None:
    ws = worksheet(google_credentials())
    preview_col, pending = get_pending_rows(ws, limit)
    if not pending:
        print("Không có dòng nào cần khôi phục Preview.")
        return

    drive_folder_id = required_env("GOOGLE_DRIVE_PREVIEW_FOLDER_ID")
    drive = build_drive()
    requests: list[dict[str, object]] = []
    found = 0
    not_found = 0

    for row_number, link in pending:
        try:
            filename = image_filename(link)
            file_id = find_drive_file(drive, drive_folder_id, filename)
            if not file_id:
                not_found += 1
                print(f"KHÔNG CÓ TRÊN DRIVE dòng {row_number}: {filename}")
                continue
            requests.append(preview_request(row_number, preview_col, file_id))
            found += 1
        except Exception as exc:
            print(f"LỖI recover dòng {row_number}: {exc}")

    written = 0
    for start in range(0, len(requests), 500):
        chunk = requests[start:start + 500]
        batch_write_previews(ws, chunk)
        written += len(chunk)
        print(f"RECOVER: đã ghi {written}/{len(requests)} Preview từ Drive")

    print(
        f"Hoàn tất khôi phục: tìm thấy {found} ảnh trên Drive, "
        f"đã ghi {written} Preview, {not_found} dòng chưa có ảnh trên Drive."
    )


async def download_new_previews(limit: int | None) -> None:
    ws = worksheet(google_credentials())
    preview_col, pending = get_pending_rows(ws, limit)
    if not pending:
        print("Không có dòng nào cần lấy Preview mới.")
        return

    api_id_raw = required_env("TELEGRAM_API_ID")
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID phải là số nguyên") from exc

    api_hash = required_env("TELEGRAM_API_HASH")
    session = required_env("TELEGRAM_SESSION")
    drive_folder_id = required_env("GOOGLE_DRIVE_PREVIEW_FOLDER_ID")
    drive = build_drive()
    temp_dir = Path("preview_temp")
    temp_dir.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    uploaded = 0
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("TELEGRAM_SESSION hết hạn hoặc chưa đăng nhập")

        entity_cache: dict[int | str, object] = {}
        for row_number, link in pending:
            destination: Path | None = None
            try:
                filename = image_filename(link)
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

    print(f"Hoàn tất: lấy mới và ghi ngay {uploaded} Preview vào Sheet.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lấy Preview mới từ Telegram hoặc khôi phục Preview từ ảnh đã có trên Google Drive"
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--mode",
        choices=("new", "recover"),
        default="new",
        help="new = lấy Preview mới từ Telegram; recover = chỉ khôi phục Preview từ Drive",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit phải lớn hơn 0")

    if args.mode == "recover":
        recover_previews(args.limit)
    else:
        asyncio.run(download_new_previews(args.limit))


if __name__ == "__main__":
    main()
