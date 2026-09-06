from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Channel


HEADER_ROW = 1
COL_CODE = "Mã"
COL_TELEGRAM_LINK = "Telegram_video_link"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CODE_PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9])([A-Za-z]+[0-9]{3,})(?![A-Za-z0-9])")


@dataclass(frozen=True)
class FoundVideo:
    code: str
    link: str
    date: datetime


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise EnvironmentError(f"Thiếu biến môi trường: {name}")
    return value


def normalize_header(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())


def get_worksheet():
    sheet_id = required_env("GOOGLE_SHEET_ID")
    credentials_json = required_env("GOOGLE_CREDENTIALS")
    tab_name = os.getenv("GOOGLE_SHEET_TAB", "Kho link video tele")
    try:
        info = json.loads(credentials_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GOOGLE_CREDENTIALS không phải JSON hợp lệ: {exc}") from exc
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(sheet_id).worksheet(tab_name)


def parse_start_date(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value.strip(), "%d/%m/%Y")
    except ValueError as exc:
        raise ValueError("Ngày phải có định dạng d/m/yyyy, ví dụ 6/9/2026") from exc
    return parsed.replace(tzinfo=timezone.utc)


def parse_codes(value: str | None) -> set[str] | None:
    if not value or not value.strip():
        return None
    parts = re.split(r"[\s,;]+", value.strip())
    codes: set[str] = set()
    invalid: list[str] = []
    for raw in parts:
        if not raw:
            continue
        code = raw.upper()
        if not re.fullmatch(r"[A-Z]+[0-9]{3,}", code):
            invalid.append(raw)
            continue
        codes.add(code)
    if invalid:
        raise ValueError(
            "Mã không hợp lệ: " + ", ".join(invalid) + ". Mã phải là chữ + ít nhất 3 chữ số, ví dụ MDU4382."
        )
    return codes or None


def extract_code(filename: str | None) -> str | None:
    if not filename:
        return None
    name = filename.strip()
    if name.lower().endswith(".mp4"):
        name = name[:-4]
    match = CODE_PATTERN.search(name)
    return match.group(1).upper() if match else None


def message_link(entity: object, message_id: int) -> str | None:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"

    entity_id = getattr(entity, "id", None)
    if isinstance(entity, Channel) and entity_id:
        return f"https://t.me/c/{entity_id}/{message_id}"
    return None


def existing_links(ws) -> set[str]:
    values = ws.get_all_values()
    if not values:
        raise ValueError("Tab Kho link video tele đang trống, cần có hàng tiêu đề ở hàng 1")

    headers = values[HEADER_ROW - 1]
    header_map = {
        normalize_header(value): index
        for index, value in enumerate(headers)
        if normalize_header(value)
    }
    code_idx = header_map.get(normalize_header(COL_CODE))
    link_idx = header_map.get(normalize_header(COL_TELEGRAM_LINK))
    missing = []
    if code_idx is None:
        missing.append(COL_CODE)
    if link_idx is None:
        missing.append(COL_TELEGRAM_LINK)
    if missing:
        raise ValueError(f"Sheet thiếu cột bắt buộc ở hàng 1: {', '.join(missing)}")

    links: set[str] = set()
    for row in values[HEADER_ROW:]:
        if link_idx < len(row):
            link = row[link_idx].strip()
            if link:
                links.add(link)
    return links


def append_found(ws, found: list[FoundVideo]) -> None:
    if not found:
        print("Không có video mới cần ghi.")
        return

    headers = ws.row_values(HEADER_ROW)
    header_map = {
        normalize_header(value): index
        for index, value in enumerate(headers)
        if normalize_header(value)
    }
    code_idx = header_map[normalize_header(COL_CODE)]
    link_idx = header_map[normalize_header(COL_TELEGRAM_LINK)]

    rows: list[list[str]] = []
    width = len(headers)
    for item in found:
        row = [""] * width
        row[code_idx] = item.code
        row[link_idx] = item.link
        rows.append(row)

    ws.append_rows(rows, value_input_option="RAW", insert_data_option="INSERT_ROWS")
    print(f"Đã ghi {len(rows)} video mới vào tab Kho link video tele.")


def select_latest_per_code(
    found: list[FoundVideo],
    target_codes: set[str] | None,
    latest_per_code: int | None,
) -> list[FoundVideo]:
    if latest_per_code is None:
        return sorted(found, key=lambda item: item.date, reverse=True)

    grouped: dict[str, list[FoundVideo]] = {}
    for item in found:
        grouped.setdefault(item.code, []).append(item)

    selected: list[FoundVideo] = []
    codes_to_process = sorted(target_codes) if target_codes else sorted(grouped)
    for code in codes_to_process:
        items = sorted(grouped.get(code, []), key=lambda item: item.date, reverse=True)
        selected.extend(items[:latest_per_code])
        print(f"Mã {code}: lấy {min(len(items), latest_per_code)}/{latest_per_code} video mới nhất chưa có trong Sheet.")

    return sorted(selected, key=lambda item: (item.code, item.date), reverse=True)


async def scan(
    *,
    start_date: datetime,
    target_codes: set[str] | None,
    latest_per_code: int | None,
) -> None:
    ws = get_worksheet()
    known_links = existing_links(ws)

    try:
        api_id = int(required_env("TELEGRAM_API_ID"))
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID phải là số nguyên") from exc

    client = TelegramClient(
        StringSession(required_env("TELEGRAM_SESSION")),
        api_id,
        required_env("TELEGRAM_API_HASH"),
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("TELEGRAM_SESSION hết hạn hoặc chưa đăng nhập")

        found: list[FoundVideo] = []
        seen_this_run = set(known_links)
        scanned_chats = 0
        scanned_messages = 0

        if target_codes:
            print("Chỉ quét các mã: " + ", ".join(sorted(target_codes)))
        else:
            print("Không nhập danh sách mã: quét tất cả mã hợp lệ.")

        async for dialog in client.iter_dialogs():
            if not (dialog.is_group or dialog.is_channel):
                continue
            scanned_chats += 1
            entity = dialog.entity
            print(f"Đang quét: {dialog.name}")

            async for message in client.iter_messages(entity):
                message_date = message.date
                if message_date is None:
                    continue
                if message_date < start_date:
                    break

                scanned_messages += 1
                if not message.media:
                    continue
                filename = getattr(message.file, "name", None) if message.file else None
                if not filename or not filename.lower().endswith(".mp4"):
                    continue

                code = extract_code(filename)
                if not code:
                    continue
                if target_codes is not None and code not in target_codes:
                    continue

                link = message_link(entity, message.id)
                if not link or link in seen_this_run:
                    continue

                found.append(FoundVideo(code=code, link=link, date=message_date))
                seen_this_run.add(link)

        selected = select_latest_per_code(found, target_codes, latest_per_code)
        for item in selected:
            print(f"  + {item.code}: {item.link} ({item.date.strftime('%d/%m/%Y %H:%M')})")

        append_found(ws, selected)
        print(
            f"Hoàn tất: quét {scanned_chats} nhóm/kênh, {scanned_messages} tin nhắn từ "
            f"{start_date.strftime('%d/%m/%Y')}, tìm {len(found)} video mới phù hợp, "
            f"ghi {len(selected)} video vào Sheet."
        )
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Quét nhóm/kênh Telegram, lấy mã video và link chưa có trong Google Sheet"
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="Chỉ quét tin nhắn từ ngày này trở đi, định dạng d/m/yyyy; ví dụ 6/9/2026",
    )
    parser.add_argument(
        "--codes",
        default="",
        help="Danh sách mã cần quét; có thể xuống dòng, phân tách bằng dấu phẩy, chấm phẩy hoặc khoảng trắng",
    )
    parser.add_argument(
        "--latest-per-code",
        type=int,
        default=None,
        help="Chỉ lấy N video mới nhất chưa có trong Sheet cho mỗi mã",
    )
    args = parser.parse_args()
    if args.latest_per_code is not None and args.latest_per_code <= 0:
        raise ValueError("--latest-per-code phải lớn hơn 0")

    asyncio.run(
        scan(
            start_date=parse_start_date(args.start_date),
            target_codes=parse_codes(args.codes),
            latest_per_code=args.latest_per_code,
        )
    )


if __name__ == "__main__":
    main()
