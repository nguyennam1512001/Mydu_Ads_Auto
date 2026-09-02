"""
Đọc danh sách campaign cần tạo từ Google Sheet, và ghi kết quả (thành công/lỗi)
ngược lại vào sheet sau khi chạy.

Xác thực bằng Service Account (không cần đăng nhập tay, sheet có thể để riêng tư -
chỉ cần share sheet cho email của Service Account).

Credentials được đọc TRỰC TIẾP từ biến môi trường GOOGLE_CREDENTIALS (nội dung
JSON key dạng text, y hệt cách đặt secret trên GitHub Actions) - không ghi ra
file trung gian, tránh lỗi hỏng định dạng JSON (ký tự \\r, encoding...) khi ghi
qua shell.

Các cột được tìm theo tên tiêu đề ở hàng 1, không phụ thuộc vào
vị trí A, B, C... Vì vậy có thể thêm, xóa hoặc di chuyển cột mà không
cần sửa lại thứ tự cột trong file này.
"""
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Tên tiêu đề cột ở hàng 1 trong Google Sheet.
# Giữ nguyên các tên biến COL_...; giá trị bây giờ là tên cột thay vì A, B, C...
COL_AD_ACCOUNT_ID = "AD_ACCOUNT_ID"
COL_PAGE_ID = "PAGE_ID"
COL_SCHEDULE_DATE = "SCHEDULE_DATE"
COL_SCHEDULE_TIME = "SCHEDULE_TIME"
COL_CAMPAIGN_NAME = "CAMPAIGN_NAME"
COL_GROUP_AD_NAME = "Mã"
COL_DAILY_BUDGET = "DAILY_BUDGET"
COL_POST_ID = "POST_ID"
COL_MESSAGE_TEMPLATE = "CHAT_TEMPLATE"
COL_GENDER = "Gender"
COL_AGE = "Age"
COL_RESULT = "RESULT"

HEADER_ROW = 1
FIRST_DATA_ROW = 2
_RESULT_COLUMN_CACHE: dict[int, int] = {}


@dataclass
class SheetRow:
    row_number: int
    ad_account_id: str
    page_id: str
    campaign_name: str
    daily_budget: int
    post_id: str
    schedule: str | None = None
    group_ad_name: str | None = None 
    message_template_name: str | None = None
    age_min: int | None = None
    age_max: int | None = None
    genders: list[int] | None = None

def _get_client() -> gspread.Client:
    """
    Ưu tiên đọc credentials từ GOOGLE_CREDENTIALS (nội dung JSON dạng text,
    dùng cho GitHub Actions secret). Nếu không có, fallback sang
    GOOGLE_SERVICE_ACCOUNT_FILE (đường dẫn file JSON, dùng khi chạy local với
    file key sẵn trên máy) để tương thích ngược.
    """
    raw_json = os.getenv("GOOGLE_CREDENTIALS")
    if raw_json:
        try:
            info = json.loads(raw_json)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"GOOGLE_CREDENTIALS không phải JSON hợp lệ: {e}. "
                "Kiểm tra lại đã dán ĐÚNG NGUYÊN nội dung file JSON key vào secret chưa."
            ) from e
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return gspread.authorize(creds)

    key_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not key_path:
        raise EnvironmentError(
            "Thiếu credentials Google: cần GOOGLE_CREDENTIALS (nội dung JSON) "
            "hoặc GOOGLE_SERVICE_ACCOUNT_FILE (đường dẫn file JSON). "
            "Xem README phần 'Kết nối Google Sheet'."
        )
    if not os.path.exists(key_path):
        raise FileNotFoundError(f"Không tìm thấy file key Service Account: {key_path}")

    creds = Credentials.from_service_account_file(key_path, scopes=SCOPES)
    return gspread.authorize(creds)


def get_worksheet() -> gspread.Worksheet:
    """Mở đúng tab (worksheet) trong Google Sheet dựa theo GOOGLE_SHEET_ID / GOOGLE_SHEET_TAB."""
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise EnvironmentError("Thiếu GOOGLE_SHEET_ID trong .env")
    tab_name = os.getenv("GOOGLE_SHEET_TAB", "Data")

    client = _get_client()
    spreadsheet = client.open_by_key(sheet_id)
    return spreadsheet.worksheet(tab_name)


def _parse_budget(raw: str) -> int:
    """'3.000.000 đ' hoặc '3000000' -> 3000000. VND không có phần thập phân."""
    digits = re.sub(r"[^\d]", "", raw or "")
    if not digits:
        raise ValueError(f"không đọc được số tiền từ giá trị '{raw}'")
    return int(digits)
    
def _parse_age(raw: str) -> tuple[int | None, int | None]:
    """'27-55' -> (27, 55); để trống -> Meta tự động."""
    raw = (raw or "").strip()
    if not raw:
        return None, None

    match = re.fullmatch(r"(\d{1,2})\s*[-–—]\s*(\d{1,2})", raw)
    if not match:
        raise ValueError("Age phải có dạng 27-55 hoặc để trống")

    age_min, age_max = (int(value) for value in match.groups())
    if not 18 <= age_min <= age_max <= 65:
        raise ValueError("Age phải nằm trong khoảng 18-65 và tuổi đầu không lớn hơn tuổi cuối")
    return age_min, age_max


def _parse_gender(raw: str) -> list[int] | None:
    """Nam/1 -> [1], Nữ/2 -> [2], để trống -> tất cả giới tính."""
    normalized = (raw or "").strip().casefold()
    if not normalized:
        return None
    if normalized in {"nam", "1"}:
        return [1]
    if normalized in {"nữ", "nu", "2"}:
        return [2]
    raise ValueError("Gender chỉ nhận Nam/1, Nữ/2 hoặc để trống")


def _parse_schedule(date_raw: str, time_raw: str) -> str | None:
    """
    Ngày 'd/m' (VD: 30/8) + giờ 'HH:MM' (VD: 00:00) -> ISO 8601 có timezone.
    Ngày không có năm -> lấy năm hiện tại; nếu ngày đó đã qua trong năm nay
    thì tự chuyển sang năm sau (tránh đặt lịch chạy vào quá khứ).
    Bỏ trống cả 2 cột -> chạy ngay (None).
    """
    date_raw = (date_raw or "").strip()
    time_raw = (time_raw or "").strip()
    if not date_raw and not time_raw:
        return None
    if not date_raw or not time_raw:
        raise ValueError("lịch chạy thiếu Ngày hoặc giờ (phải điền cả 2 hoặc để trống cả 2)")

    try:
        day, month = (int(x) for x in date_raw.split("/"))
        hour, minute = (int(x) for x in time_raw.split(":"))
    except ValueError as e:
        raise ValueError(
            f"lịch chạy '{date_raw} {time_raw}' sai định dạng "
            "(Ngày phải là d/m, giờ phải là HH:MM)"
        ) from e

    now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
    dt = datetime(now.year, month, day, hour, minute, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    if dt < now:
        dt = dt.replace(year=now.year + 1)

    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")

def _normalize_header(value: str) -> str:
    """Chuẩn hóa tên cột, không phân biệt hoa/thường và khoảng trắng thừa."""
    return " ".join((value or "").strip().casefold().split())


def _build_header_map(header_row: list[str]) -> dict[str, int]:
    """Tạo ánh xạ tên cột ở hàng 1 sang vị trí cột (tính từ 0)."""
    header_map: dict[str, int] = {}
    for index, header in enumerate(header_row):
        normalized = _normalize_header(header)
        if normalized:
            header_map[normalized] = index
    return header_map


def _col_to_index(header_map: dict[str, int], column_name: str) -> int:
    """Lấy vị trí cột dựa trên tên tiêu đề ở hàng 1."""
    normalized = _normalize_header(column_name)
    if normalized not in header_map:
        raise ValueError(
            f"Không tìm thấy cột '{column_name}' ở hàng {HEADER_ROW} "
            "của Google Sheet"
        )
    return header_map[normalized]


def read_rows(worksheet: gspread.Worksheet) -> list[SheetRow]:
    """
    Đọc toàn bộ dòng có dữ liệu trong sheet.

    - Bỏ qua dòng trống hoàn toàn.
    - Bỏ qua dòng đã có giá trị ở cột Kết quả - coi như đã chạy trước đó,
      tránh tạo trùng campaign khi chạy lại script nhiều lần.
    - Dòng thiếu dữ liệu bắt buộc hoặc sai định dạng ngân sách sẽ được ghi thẳng
      "Lỗi: ..." vào cột Kết quả và bị bỏ qua, không đưa vào danh sách trả về.
    """
    values = worksheet.get_all_values()

    if not values:
        return []

    header_map = _build_header_map(values[HEADER_ROW - 1])

    idx_account = _col_to_index(header_map, COL_AD_ACCOUNT_ID)
    idx_page = _col_to_index(header_map, COL_PAGE_ID)
    idx_name = _col_to_index(header_map, COL_CAMPAIGN_NAME)
    idx_group_ad_name = _col_to_index(header_map, COL_GROUP_AD_NAME)
    idx_budget = _col_to_index(header_map, COL_DAILY_BUDGET)
    idx_schedule_date = _col_to_index(header_map, COL_SCHEDULE_DATE)
    idx_schedule_time = _col_to_index(header_map, COL_SCHEDULE_TIME)
    idx_post = _col_to_index(header_map, COL_POST_ID)
    idx_message_template = _col_to_index(header_map, COL_MESSAGE_TEMPLATE)
    idx_gender = _col_to_index(header_map, COL_GENDER)
    idx_age = _col_to_index(header_map, COL_AGE)
    idx_result = _col_to_index(header_map, COL_RESULT)
    _RESULT_COLUMN_CACHE[id(worksheet)] = idx_result + 1

    def cell(row: list[str], idx: int) -> str:
        return row[idx].strip() if idx < len(row) else ""

    rows: list[SheetRow] = []
    for row_number, row in enumerate(values[HEADER_ROW:], start=FIRST_DATA_ROW):
        ad_account_id = cell(row, idx_account)
        page_id = cell(row, idx_page)
        campaign_name = cell(row, idx_name)
        group_ad_name = cell(row, idx_group_ad_name)
        budget_raw = cell(row, idx_budget)
        schedule_date_raw = cell(row, idx_schedule_date) 
        schedule_time_raw = cell(row, idx_schedule_time) 
        post_id = cell(row, idx_post)
        message_template_name = cell(row, idx_message_template)
        gender_raw = cell(row, idx_gender)
        age_raw = cell(row, idx_age)
        result = cell(row, idx_result)

        if not any([ad_account_id, page_id, campaign_name, budget_raw, post_id]):
            continue  # dòng trống

        if result:
            continue  # đã chạy trước đó (đã có kết quả)

        missing = [
            label
            for label, val in [
                ("ID tài khoản", ad_account_id),
                ("ID PAGE", page_id),
                ("Tên Campaign", campaign_name),
                ("Ngân sách", budget_raw),
                ("ID POST", post_id),
            ]
            if not val
        ]
        if missing:
            write_result(worksheet, row_number, f"Lỗi: thiếu {', '.join(missing)}")
            continue

        try:
            daily_budget = _parse_budget(budget_raw)
            schedule = _parse_schedule(schedule_date_raw, schedule_time_raw)
            age_min, age_max = _parse_age(age_raw)
            genders = _parse_gender(gender_raw)
        except ValueError as e:
            write_result(worksheet, row_number, f"Lỗi: {e}")
            continue

        rows.append(
            SheetRow(
                row_number=row_number,
                ad_account_id=ad_account_id,
                page_id=page_id,
                campaign_name=campaign_name,
                group_ad_name=group_ad_name or None,
                daily_budget=daily_budget,
                post_id=post_id,
                schedule=schedule,
                message_template_name=message_template_name or None,
                age_min=age_min,
                age_max=age_max,
                genders=genders,
            )
        )

    return rows


def write_result(worksheet: gspread.Worksheet, row_number: int, message: str) -> None:
    """Ghi kết quả vào cột có tên COL_RESULT ở hàng 1."""
    result_column = _RESULT_COLUMN_CACHE.get(id(worksheet))
    if result_column is None:
        header_map = _build_header_map(worksheet.row_values(HEADER_ROW))
        result_column = _col_to_index(header_map, COL_RESULT) + 1
        _RESULT_COLUMN_CACHE[id(worksheet)] = result_column
    cell_address = gspread.utils.rowcol_to_a1(row_number, result_column)
    worksheet.update_acell(cell_address, message)
