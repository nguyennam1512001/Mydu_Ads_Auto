from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from telethon import TelegramClient
from telethon.sessions import StringSession


HEADER_ROW = 1
COL_TELEGRAM_LINK = "Telegram_video_link"
COL_PREVIEW_IMAGE = "Preview_Image"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
PRIVATE_LINK = re.compile(r"^/c/(?P<channel>\d+)/(?P<message>\d+)/?$")
PUBLIC_LINK = re.compile(r"^/(?P<username>[A-Za-z][A-Za-z0-9_]{3,})/(?P<message>\d+)/?$")


def normalize_header(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise EnvironmentError(f"Thiếu biến môi trường: {name}")
    return value


def google_credentials() -> Credentials:
    credentials_json = required_env("GOOGLE_CREDENTIALS")
    try:
        info = json.loads(credentials_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GOOGLE_CREDENTIALS không phải JSON hợp lệ: {exc}") from exc
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def worksheet(creds: Credentials | None = None):
    sheet_id = required_env("GOOGLE_SHEET_ID")
    tab_name = os.getenv("GOOGLE_SHEET_TAB", "Bài viết")
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
    digest = hashlib.sha1(link.strip().encode("utf-8")).hexdigest()[:20]
    return f"telegram_{digest}.jpg"


def drive_image_url(file_id: str) -> str:
    return (
        "https://drive.usercontent.google.com/download"
        f"?id={file_id}&export=view&authuser=0"
    )


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
        ensure_public_reader(drive, existing_id)
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


async def download_previews(limit: int | None, output: Path) -> None:
    creds = google_credentials()
    ws = worksheet(creds)
    values = ws.get_all_values()
    if not values:
        raise ValueError("Tab Bài viết đang trống")

    headers = values[HEADER_ROW - 1]
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
        output.write_text("[]", encoding="utf-8")
        print("Không có dòng nào cần lấy Preview_Image.")
        return

    api_id_raw = required_env("TELEGRAM_API_ID")
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID phải là số nguyên") from exc

    api_hash = required_env("TELEGRAM_API_HASH")
    session = required_env("TELEGRAM_SESSION")
    drive_folder_id = required_env("GOOGLE_DRIVE_PREVIEW_FOLDER_ID")
    temp_dir = Path("preview_temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    updates: list[dict[str, object]] = []
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("TELEGRAM_SESSION hết hạn hoặc chưa đăng nhập")

        entity_cache: dict[int | str, object] = {}
        for row_number, link in pending:
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

                filename = image_filename(link)
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
                url = drive_image_url(file_id)
                updates.append({"row": row_number, "url": url, "file_id": file_id})
                print(f"OK dòng {row_number}: {filename} -> Drive {file_id}")
            except Exception as exc:
                print(f"LỖI dòng {row_number}: {exc}")
            finally:
                if destination and destination.exists():
                    destination.unlink()
    finally:
        await client.disconnect()

    output.write_text(json.dumps(updates, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã upload {len(updates)} ảnh xem trước lên Google Drive.")


def apply_updates(input_path: Path) -> None:
    if not input_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file cập nhật: {input_path}")
    updates_data = json.loads(input_path.read_text(encoding="utf-8"))
    if not updates_data:
        print("Không có Preview_Image cần ghi vào Sheet.")
        return

    ws = worksheet()
    headers = ws.row_values(HEADER_ROW)
    header_map = {
        normalize_header(value): index + 1
        for index, value in enumerate(headers)
        if normalize_header(value)
    }
    preview_col = header_map.get(normalize_header(COL_PREVIEW_IMAGE))
    if not preview_col:
        raise ValueError(f"Sheet thiếu cột bắt buộc: {COL_PREVIEW_IMAGE}")

    requests = []
    for item in updates_data:
        row = int(item["row"])
        url = str(item["url"])
        formula = f'=IMAGE("{url}")'
        requests.append({
            "range": gspread.utils.rowcol_to_a1(row, preview_col),
            "values": [[formula]],
        })

    ws.batch_update(requests, value_input_option="USER_ENTERED")
    print(f"Đã ghi {len(requests)} Preview_Image vào tab Bài viết.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lấy thumbnail video Telegram, upload Google Drive và ghi Preview_Image vào Google Sheet"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("--limit", type=int, default=None)
    download_parser.add_argument("--output", default="preview_updates.json")

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--input", default="preview_updates.json")

    args = parser.parse_args()
    if args.command == "download":
        if args.limit is not None and args.limit <= 0:
            raise ValueError("--limit phải lớn hơn 0")
        asyncio.run(download_previews(args.limit, Path(args.output)))
    else:
        apply_updates(Path(args.input))


if __name__ == "__main__":
    main()
