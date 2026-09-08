from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from telethon import TelegramClient
from telethon.sessions import StringSession


HEADER_ROW = 1
GROUP_NAME = "Các bài ads chạy tốt <150k"
SHEET_TAB = "Các bài ads chạy tốt <150k"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v26.0").strip() or "v26.0"

ACTION_SCAN = "Quét bài ads <150k"
ACTION_VIDEO_ID = "Lấy Video ID"

COL_CODE = "Mã"
COL_DATE = "Ngày"
COL_PAGE = "Page"
COL_ADS_SP = "ADS/SP"
COL_PERMALINK = "Permalink"
COL_VIDEO_ID = "Video id"


FACEBOOK_URL_RE = re.compile(r"https?://(?:www\.)?facebook\.com/[^\s<>]+", re.IGNORECASE)
POST_URL_RE = re.compile(
    r"https?://(?:www\.)?facebook\.com/(?P<page>\d+)/posts/(?P<post>\d+)",
    re.IGNORECASE,
)
VIDEO_URL_RE = re.compile(
    r"https?://(?:www\.)?facebook\.com/(?:[^/]+/)?videos/(?P<video>\d+)",
    re.IGNORECASE,
)
REEL_URL_RE = re.compile(
    r"https?://(?:www\.)?facebook\.com/(?:reel|reels)/(?P<video>\d+)",
    re.IGNORECASE,
)

_PAGE_TOKEN_CACHE: dict[str, str] = {}


@dataclass(frozen=True)
class GoodAd:
    code: str
    date: str
    page: str
    ads_sp: str
    permalink: str
    video_id: str


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise EnvironmentError(f"Thiếu biến môi trường: {name}")
    return value


def normalize_header(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())


def normalize_group_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u00a0", " ")
    return " ".join(value.casefold().strip().split())


def get_spreadsheet():
    sheet_id = required_env("GOOGLE_SHEET_ID")
    credentials_json = required_env("GOOGLE_CREDENTIALS")
    try:
        info = json.loads(credentials_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GOOGLE_CREDENTIALS không phải JSON hợp lệ: {exc}") from exc

    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(sheet_id)


def get_header_map(ws) -> tuple[list[str], dict[str, int]]:
    headers = ws.row_values(HEADER_ROW)
    if not headers:
        raise ValueError(f"Tab {ws.title} cần có hàng tiêu đề ở hàng 1")

    header_map = {
        normalize_header(value): index
        for index, value in enumerate(headers)
        if normalize_header(value)
    }
    required = [COL_CODE, COL_DATE, COL_PAGE, COL_ADS_SP, COL_PERMALINK, COL_VIDEO_ID]
    missing = [name for name in required if normalize_header(name) not in header_map]
    if missing:
        raise ValueError("Sheet thiếu cột bắt buộc: " + ", ".join(missing))
    return headers, header_map


def existing_permalinks(ws) -> set[str]:
    values = ws.get_all_values()
    if not values:
        return set()
    _, header_map = get_header_map(ws)
    idx = header_map[normalize_header(COL_PERMALINK)]
    result: set[str] = set()
    for row in values[HEADER_ROW:]:
        if idx < len(row):
            value = row[idx].strip()
            if value:
                result.add(value)
    return result


def clean_value(value: str) -> str:
    return value.strip().strip("`*_ ")


def extract_labeled_value(text: str, labels: list[str]) -> str:
    escaped = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?im)^\s*(?:{escaped})\s*:\s*(.+?)\s*$", text)
    return clean_value(match.group(1)) if match else ""


def parse_message(text: str) -> tuple[str, str, str, str, str] | None:
    if not text:
        return None

    code = extract_labeled_value(text, ["Mã SP", "Mã"])
    page = extract_labeled_value(text, ["Page"])
    ads_sp = extract_labeled_value(text, ["ADS/SP"])

    date_match = re.search(
        r"(?im)^\s*(?:📌\s*)?Camp\s+ngày\s+(\d{1,2}/\d{1,2}/\d{4})\s*$",
        text,
    )
    if not date_match:
        date_match = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", text)
    date_value = date_match.group(1) if date_match else ""

    url_match = FACEBOOK_URL_RE.search(text)
    permalink = url_match.group(0).rstrip(".,);]}") if url_match else ""

    if not all([code, date_value, page, ads_sp, permalink]):
        return None

    try:
        parsed_date = datetime.strptime(date_value, "%d/%m/%Y")
        date_value = parsed_date.strftime("%d/%m/%Y")
    except ValueError:
        return None

    return code.upper(), date_value, page, ads_sp, permalink


def graph_get(object_id: str, fields: str, token: str) -> dict:
    params = urllib.parse.urlencode({"fields": fields, "access_token": token})
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{urllib.parse.quote(object_id, safe='_')}?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mydu-Ads-Auto/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Meta Graph API HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Không kết nối được Meta Graph API: {exc}") from exc


def graph_get_edge(object_id: str, edge: str, fields: str, token: str) -> dict:
    """Read a Graph API edge without using deprecated Post field expansions."""
    params = urllib.parse.urlencode({"fields": fields, "access_token": token})
    quoted_object_id = urllib.parse.quote(object_id, safe="_")
    quoted_edge = urllib.parse.quote(edge.strip("/"), safe="")
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{quoted_object_id}/{quoted_edge}?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mydu-Ads-Auto/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Meta Graph API HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Không kết nối được Meta Graph API: {exc}") from exc


def get_page_access_token(page_id: str, token: str) -> str:
    cached = _PAGE_TOKEN_CACHE.get(page_id)
    if cached:
        return cached

    payload = graph_get(page_id, "access_token", token)
    page_token = str(payload.get("access_token") or "").strip() or token
    _PAGE_TOKEN_CACHE[page_id] = page_token

    if page_token != token:
        print(f"Đã lấy Page Access Token cho Page {page_id}.")
    else:
        print(
            f"CẢNH BÁO Page {page_id}: Meta không trả Page Access Token riêng; "
            "đang dùng FB_ACCESS_TOKEN hiện tại."
        )
    return page_token


def video_id_from_attachment(node: object) -> str:
    if not isinstance(node, dict):
        return ""

    attachment_type = str(node.get("media_type") or "").casefold()
    target = node.get("target")
    if "video" in attachment_type and isinstance(target, dict):
        target_id = str(target.get("id") or "").strip()
        if target_id:
            return target_id

    subattachments = node.get("subattachments")
    if isinstance(subattachments, dict):
        for child in subattachments.get("data") or []:
            found = video_id_from_attachment(child)
            if found:
                return found
    return ""


def _video_id_from_url(url: str) -> str:
    for pattern in (VIDEO_URL_RE, REEL_URL_RE):
        match = pattern.search(url or "")
        if match:
            return match.group("video")
    return ""


def post_ref_from_permalink(permalink: str) -> tuple[str, str]:
    match = POST_URL_RE.search(permalink or "")
    if not match:
        return "", ""
    return match.group("page"), match.group("post")


def page_id_from_permalink(permalink: str) -> str:
    return post_ref_from_permalink(permalink)[0]


def is_page_permission_error(exc: Exception) -> bool:
    text = str(exc).casefold()
    return (
        "pages_read_engagement" in text
        or "page public content access" in text
        or "pages_read_user_content" in text
    )


def resolve_video_id(permalink: str, token: str) -> str:
    direct_video_id = _video_id_from_url(permalink)
    if direct_video_id:
        return direct_video_id

    post_match = POST_URL_RE.search(permalink)
    if not post_match:
        return ""

    page_id = post_match.group("page")
    post_id = post_match.group("post")
    page_token = get_page_access_token(page_id, token)
    object_id = f"{page_id}_{post_id}"

    # PagePost v26: request only current PagePost fields, then read media from
    # its dedicated edge rather than from an aggregated field expansion.
    payload = graph_get(object_id, "id,permalink_url", page_token)

    permalink_url = str(payload.get("permalink_url") or "")
    video_id = _video_id_from_url(permalink_url)
    if video_id:
        return video_id

    attachments = graph_get_edge(
        object_id,
        "attachments",
        "media_type,target,subattachments.limit(50){media_type,target}",
        page_token,
    )
    for attachment in attachments.get("data") or []:
        video_id = video_id_from_attachment(attachment)
        if video_id:
            return video_id

    return ""


def column_letter(zero_based_index: int) -> str:
    number = zero_based_index + 1
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def flush_video_updates(ws, updates: list[dict]) -> None:
    if not updates:
        return
    for start in range(0, len(updates), 200):
        chunk = updates[start : start + 200]
        ws.batch_update(chunk, value_input_option="RAW")
        print(f"Đã cập nhật Video id: {min(start + len(chunk), len(updates))}/{len(updates)} ô")


def fill_existing_video_ids(ws, token: str) -> None:
    values = ws.get_all_values()
    if not values:
        print("Sheet đang trống, không có dữ liệu để lấy Video ID.")
        return

    _, header_map = get_header_map(ws)
    permalink_idx = header_map[normalize_header(COL_PERMALINK)]
    video_idx = header_map[normalize_header(COL_VIDEO_ID)]
    code_idx = header_map[normalize_header(COL_CODE)]
    video_col = column_letter(video_idx)

    updates: list[dict] = []
    candidates = 0
    already_has_video = 0
    from_meta = 0
    no_video = 0
    meta_errors = 0
    meta_attempts = 0
    blocked_pages: set[str] = set()
    skipped_blocked_page = 0

    print("Bắt đầu lấy Video ID cho các dòng đang có trong Sheet...")

    for sheet_row, row in enumerate(values[HEADER_ROW:], start=HEADER_ROW + 1):
        permalink = row[permalink_idx].strip() if permalink_idx < len(row) else ""
        current_video = row[video_idx].strip() if video_idx < len(row) else ""
        code = row[code_idx].strip() if code_idx < len(row) else f"dòng {sheet_row}"

        if not permalink:
            continue
        if current_video:
            already_has_video += 1
            continue

        candidates += 1

        # Permalink video/reel có ID trực tiếp thì không cần gọi API.
        direct_video_id = _video_id_from_url(permalink)
        if direct_video_id:
            from_meta += 1
            updates.append({"range": f"{video_col}{sheet_row}", "values": [[direct_video_id]]})
            print(f"+ {code} - dòng {sheet_row}: Video id = {direct_video_id} (từ URL)")
            continue

        page_id = page_id_from_permalink(permalink)
        if not token:
            no_video += 1
            continue
        if page_id and page_id in blocked_pages:
            skipped_blocked_page += 1
            no_video += 1
            continue

        meta_attempts += 1
        try:
            video_id = resolve_video_id(permalink, token)
        except Exception as exc:
            meta_errors += 1
            no_video += 1
            if page_id and is_page_permission_error(exc):
                blocked_pages.add(page_id)
                print(
                    f"CẢNH BÁO Page {page_id}: token hiện tại không có quyền đọc Page. "
                    "Các dòng còn lại của Page này sẽ không gọi Meta trong lượt chạy này."
                )
            else:
                print(f"CẢNH BÁO {code} - dòng {sheet_row}: không lấy được Video id: {exc}")
            continue

        if not video_id:
            no_video += 1
            print(f"- {code} - dòng {sheet_row}: không tìm thấy Video id")
            continue

        from_meta += 1
        updates.append({"range": f"{video_col}{sheet_row}", "values": [[video_id]]})
        print(f"+ {code} - dòng {sheet_row}: Video id = {video_id} (từ Meta API)")

    flush_video_updates(ws, updates)
    print(
        f"Hoàn tất Lấy Video ID: {candidates} dòng cần xử lý, "
        f"điền được {len(updates)}; từ URL/Meta {from_meta}; chưa lấy được {no_video}; "
        f"Meta đã gọi {meta_attempts} dòng, lỗi {meta_errors}; "
        f"bỏ qua {already_has_video} dòng đã có Video id, "
        f"bỏ gọi Meta nhanh {skipped_blocked_page} dòng thuộc Page thiếu quyền."
    )


def append_rows(ws, items: list[GoodAd]) -> None:
    if not items:
        print("Không có tin nhắn mới cần ghi.")
        return

    headers, header_map = get_header_map(ws)
    width = len(headers)
    rows: list[list[str]] = []

    for item in items:
        row = [""] * width
        row[header_map[normalize_header(COL_CODE)]] = item.code
        row[header_map[normalize_header(COL_DATE)]] = item.date
        row[header_map[normalize_header(COL_PAGE)]] = item.page
        row[header_map[normalize_header(COL_ADS_SP)]] = item.ads_sp
        row[header_map[normalize_header(COL_PERMALINK)]] = item.permalink
        row[header_map[normalize_header(COL_VIDEO_ID)]] = item.video_id
        rows.append(row)

    ws.append_rows(rows, value_input_option="USER_ENTERED", insert_data_option="INSERT_ROWS")
    print(f"Đã ghi {len(rows)} dòng vào tab {SHEET_TAB}.")


async def find_group(client: TelegramClient):
    wanted = normalize_group_name(GROUP_NAME)
    fallback = None
    visible_groups: list[str] = []

    async for dialog in client.iter_dialogs():
        if not (dialog.is_group or dialog.is_channel):
            continue

        raw_name = getattr(dialog, "name", "") or ""
        name = normalize_group_name(raw_name)
        visible_groups.append(raw_name)

        if name == wanted:
            print(f"Đã khớp chính xác group Telegram: {raw_name}")
            return dialog.entity

        if "các bài ads chạy tốt" in name and "150k" in name:
            fallback = dialog.entity
            print(f"Đã tìm thấy group gần khớp: {raw_name}")

    if fallback is not None:
        return fallback

    likely = [
        name
        for name in visible_groups
        if "ads" in normalize_group_name(name) or "150k" in normalize_group_name(name)
    ]
    if likely:
        print("Các group/channel gần giống mà TELEGRAM_SESSION đang nhìn thấy:")
        for name in likely[:20]:
            print(f"- {name}")
    else:
        print(
            f"TELEGRAM_SESSION nhìn thấy {len(visible_groups)} group/channel nhưng "
            f"không có tên gần giống '{GROUP_NAME}'."
        )

    raise RuntimeError(
        f"Không tìm thấy group Telegram: {GROUP_NAME}. "
        "Hãy kiểm tra tài khoản dùng để tạo TELEGRAM_SESSION có đang là thành viên của group này không."
    )


async def scan_telegram_good_ads(ws) -> None:
    known_permalinks = existing_permalinks(ws)
    token = os.getenv("FB_ACCESS_TOKEN", "").strip()
    if not token:
        print("CẢNH BÁO: Thiếu FB_ACCESS_TOKEN -> vẫn ghi dữ liệu nhưng cột Video id sẽ để trống.")

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

        entity = await find_group(client)
        print(f"Đã tìm thấy group: {GROUP_NAME}")
        print("Bắt đầu quét toàn bộ lịch sử tin nhắn khả dụng...")

        pending: list[GoodAd] = []
        seen_permalinks = set(known_permalinks)
        scanned = 0
        valid = 0
        skipped_existing = 0
        video_found = 0
        video_errors = 0
        blocked_pages: set[str] = set()

        async for message in client.iter_messages(entity, reverse=True):
            scanned += 1
            text = message.raw_text or ""
            parsed = parse_message(text)
            if parsed is None:
                continue

            valid += 1
            code, date_value, page, ads_sp, permalink = parsed
            if permalink in seen_permalinks:
                skipped_existing += 1
                continue

            video_id = ""
            if token:
                page_id = page_id_from_permalink(permalink)
                if not page_id or page_id not in blocked_pages:
                    try:
                        video_id = resolve_video_id(permalink, token)
                        if video_id:
                            video_found += 1
                            print(f"+ {code}: Video id = {video_id}")
                        else:
                            print(f"+ {code}: không tìm thấy Video id từ Meta")
                    except Exception as exc:
                        video_errors += 1
                        if page_id and is_page_permission_error(exc):
                            blocked_pages.add(page_id)
                            print(
                                f"CẢNH BÁO Page {page_id}: thiếu quyền đọc Page; "
                                "các tin còn lại của Page này sẽ không gọi Meta trong lần chạy."
                            )
                        else:
                            print(f"CẢNH BÁO {code}: không lấy được Video id: {exc}")

            pending.append(
                GoodAd(
                    code=code,
                    date=date_value,
                    page=page,
                    ads_sp=ads_sp,
                    permalink=permalink,
                    video_id=video_id,
                )
            )
            seen_permalinks.add(permalink)

        append_rows(ws, pending)
        print(
            f"Hoàn tất: quét {scanned} tin nhắn, nhận dạng {valid} tin đúng form, "
            f"bỏ {skipped_existing} permalink đã có, ghi {len(pending)} dòng, "
            f"lấy được {video_found} Video id, lỗi Meta {video_errors}."
        )
    finally:
        await client.disconnect()


async def main_async() -> None:
    book = get_spreadsheet()
    ws = book.worksheet(os.getenv("GOOGLE_SHEET_TAB", SHEET_TAB))
    action = os.getenv("GOOD_ADS_ACTION", ACTION_SCAN).strip() or ACTION_SCAN
    print(f"Chức năng được chọn: {action}")

    if action == ACTION_VIDEO_ID:
        token = os.getenv("FB_ACCESS_TOKEN", "").strip()
        if not token:
            print(
                "CẢNH BÁO: thiếu FB_ACCESS_TOKEN; không thể gọi Meta để lấy Video ID "
                "cho các Permalink không chứa trực tiếp ID video hoặc reel."
            )
        fill_existing_video_ids(ws, token)
        return

    if action != ACTION_SCAN:
        raise ValueError(f"Chức năng không hợp lệ: {action}")

    await scan_telegram_good_ads(ws)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

