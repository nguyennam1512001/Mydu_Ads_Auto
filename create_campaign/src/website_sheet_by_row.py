from __future__ import annotations

import gspread
from urllib.parse import urlparse

from src.sheet_client import (
    HEADER_ROW,
    FIRST_DATA_ROW,
    COL_AD_ACCOUNT_ID,
    COL_PAGE_ID,
    COL_CAMPAIGN_NAME,
    COL_GROUP_AD_NAME,
    COL_DAILY_BUDGET,
    COL_SCHEDULE_DATE,
    COL_SCHEDULE_TIME,
    COL_TELEGRAM_LINK,
    COL_TEXT_CONTENT,
    COL_TITLE,
    COL_WEBSITE_URL,
    COL_PIXEL,
    COL_GENDER,
    COL_AGE,
    COL_CAMP_STRUCTURE,
    COL_RESULT,
    WebsiteSalesRow,
    _build_header_map,
    _col_to_index,
    _parse_budget,
    _parse_schedule,
    _parse_age,
    _parse_gender,
    _parse_camp_structure,
    write_result,
)


def read_website_sales_rows_by_row(
    worksheet: gspread.Worksheet,
    asset_worksheet: gspread.Worksheet,
    *,
    use_existing_video: bool = False,
) -> list[WebsiteSalesRow]:
    """
    Đọc campaign Website theo đúng số hàng giữa tab Lên Camp và tab Bài viết.

    Ví dụ Lên Camp hàng 22 luôn lấy Telegram_video_link (hoặc FB_UPLOAD_ID), Text_Content, Title
    từ Bài viết hàng 22. Mã chỉ dùng làm tên/nhãn, không dùng để lookup và
    không kiểm tra trùng Mã.
    """
    values = worksheet.get_all_values()
    asset_values = asset_worksheet.get_all_values()
    if not values:
        return []
    if not asset_values:
        raise ValueError("Tab 'Bài viết' đang trống")

    header_map = _build_header_map(values[HEADER_ROW - 1])
    asset_header_map = _build_header_map(asset_values[HEADER_ROW - 1])

    required_columns = [
        COL_AD_ACCOUNT_ID,
        COL_PAGE_ID,
        COL_CAMPAIGN_NAME,
        COL_GROUP_AD_NAME,
        COL_DAILY_BUDGET,
        COL_SCHEDULE_DATE,
        COL_SCHEDULE_TIME,
        COL_WEBSITE_URL,
        COL_PIXEL,
        COL_GENDER,
        COL_AGE,
        COL_CAMP_STRUCTURE,
        COL_RESULT,
    ]
    columns = {name: _col_to_index(header_map, name) for name in required_columns}
    source_column = "FB_UPLOAD_ID" if use_existing_video else COL_TELEGRAM_LINK
    asset_columns = {
        name: _col_to_index(asset_header_map, name)
        for name in [source_column, COL_TEXT_CONTENT, COL_TITLE]
    }
    image_hash_index = asset_header_map.get("image hash")

    def cell(row: list[str], name: str) -> str:
        index = columns[name]
        return row[index].strip() if index < len(row) else ""

    def asset_cell(row: list[str], name: str) -> str:
        index = asset_columns[name]
        return row[index].strip() if index < len(row) else ""

    rows: list[WebsiteSalesRow] = []
    for row_number, row in enumerate(values[HEADER_ROW:], start=FIRST_DATA_ROW):
        raw = {name: cell(row, name) for name in required_columns}

        if not any([
            raw[COL_AD_ACCOUNT_ID],
            raw[COL_PAGE_ID],
            raw[COL_CAMPAIGN_NAME],
            raw[COL_DAILY_BUDGET],
            raw[COL_GROUP_AD_NAME],
        ]):
            continue
        if raw[COL_RESULT]:
            continue

        missing = [
            name
            for name in [
                COL_AD_ACCOUNT_ID,
                COL_PAGE_ID,
                COL_CAMPAIGN_NAME,
                COL_DAILY_BUDGET,
                COL_WEBSITE_URL,
                COL_PIXEL,
            ]
            if not raw[name]
        ]
        if missing:
            write_result(worksheet, row_number, f"Lỗi: thiếu {', '.join(missing)}")
            continue

        asset_index = row_number - 1  # list index: hàng 1 -> 0, hàng 2 -> 1...
        if asset_index >= len(asset_values):
            write_result(
                worksheet,
                row_number,
                f"Lỗi: không có hàng {row_number} tương ứng trong tab 'Bài viết'",
            )
            continue

        asset_row = asset_values[asset_index]
        source_value = asset_cell(asset_row, source_column)
        text_content = asset_cell(asset_row, COL_TEXT_CONTENT)
        title = asset_cell(asset_row, COL_TITLE)

        missing_assets = [
            name
            for name, value in [
                (source_column, source_value),
                (COL_TEXT_CONTENT, text_content),
                (COL_TITLE, title),
            ]
            if not value
        ]
        if missing_assets:
            write_result(
                worksheet,
                row_number,
                f"Lỗi: hàng {row_number} trong tab 'Bài viết' thiếu {', '.join(missing_assets)}",
            )
            continue

        try:
            if use_existing_video:
                if not source_value.isascii() or not source_value.isdigit():
                    raise ValueError("FB_UPLOAD_ID phải là một ID video dạng số")
                url = urlparse(raw[COL_WEBSITE_URL])
                if url.scheme not in ("http", "https") or not url.netloc:
                    raise ValueError("URL_Ladi phải là URL http/https hợp lệ")
                if not raw[COL_PIXEL].isascii() or not raw[COL_PIXEL].isdigit():
                    raise ValueError("Pixel phải là ID dạng số")
            daily_budget = _parse_budget(raw[COL_DAILY_BUDGET])
            if use_existing_video and daily_budget <= 0:
                raise ValueError("DAILY_BUDGET phải lớn hơn 0")
            schedule = _parse_schedule(raw[COL_SCHEDULE_DATE], raw[COL_SCHEDULE_TIME])
            age_min, age_max = _parse_age(raw[COL_AGE])
            genders = _parse_gender(raw[COL_GENDER])
            campaign_count, adset_count, ad_count = _parse_camp_structure(raw[COL_CAMP_STRUCTURE])
        except ValueError as exc:
            write_result(worksheet, row_number, f"Lỗi: {exc}")
            continue

        rows.append(
            WebsiteSalesRow(
                row_number=row_number,
                ad_account_id=raw[COL_AD_ACCOUNT_ID],
                page_id=raw[COL_PAGE_ID],
                campaign_name=raw[COL_CAMPAIGN_NAME],
                group_ad_name=raw[COL_GROUP_AD_NAME] or None,
                daily_budget=daily_budget,
                telegram_link="" if use_existing_video else source_value,
                video_id=source_value if use_existing_video else "",
                image_hash=(asset_row[image_hash_index].strip()
                            if use_existing_video and image_hash_index is not None
                            and image_hash_index < len(asset_row) else ""),
                text_content=text_content,
                title=title,
                website_url=raw[COL_WEBSITE_URL],
                pixel_id=raw[COL_PIXEL],
                campaign_count=campaign_count,
                adset_count=adset_count,
                ad_count=ad_count,
                schedule=schedule,
                age_min=age_min,
                age_max=age_max,
                genders=genders,
            )
        )

    return rows

