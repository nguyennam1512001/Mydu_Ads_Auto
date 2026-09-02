from __future__ import annotations

import json
from dataclasses import dataclass

import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
HEADER_ROW = 1

COL_PAGE_ID = "PAGE_ID"
COL_TITLE = "Title"
COL_TEXT_CONTENT = "Text_Content"
COL_TELEGRAM_LINK = "Telegram_video_link"
# Cột kỹ thuật riêng; không dùng "ID Video" vì cột đó đang chứa ARRAYFORMULA.
COL_VIDEO_ID = "FB_UPLOAD_ID"
COL_POST_ID = "POST_ID"
COL_POST_LINK = "Post Link"
COL_STATUS = "POST_STATUS"

REQUIRED_COLUMNS = [
    COL_PAGE_ID,
    COL_TEXT_CONTENT,
    COL_TELEGRAM_LINK,
    COL_VIDEO_ID,
    COL_POST_ID,
    COL_POST_LINK,
    COL_STATUS,
]


def normalize_header(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())


@dataclass(frozen=True)
class PostRow:
    row_number: int
    page_id: str
    description: str
    text_content: str
    telegram_link: str
    video_id: str
    post_id: str
    post_link: str


class SheetRepository:
    def __init__(self, sheet_id: str, tab_name: str, credentials_json: str) -> None:
        try:
            info = json.loads(credentials_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"GOOGLE_CREDENTIALS không phải JSON hợp lệ: {exc}") from exc

        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        client = gspread.authorize(creds)
        self.worksheet = client.open_by_key(sheet_id).worksheet(tab_name)
        self.header_map: dict[str, int] = {}

    def _refresh_headers(self, headers: list[str] | None = None) -> None:
        if headers is None:
            headers = self.worksheet.row_values(HEADER_ROW)
        self.header_map = {
            normalize_header(value): index + 1
            for index, value in enumerate(headers)
            if normalize_header(value)
        }

    def prepare(self, headers: list[str] | None = None) -> None:
        self._refresh_headers(headers)
        missing = [
            name for name in REQUIRED_COLUMNS
            if normalize_header(name) not in self.header_map
        ]
        if missing:
            raise ValueError(f"Sheet thiếu cột bắt buộc: {', '.join(missing)}")

    def _column(self, name: str, *, optional: bool = False) -> int | None:
        column = self.header_map.get(normalize_header(name))
        if column is None and not optional:
            raise ValueError(f"Không tìm thấy cột '{name}'")
        return column

    @staticmethod
    def _cell(row: list[str], column: int | None) -> str:
        if column is None or column > len(row):
            return ""
        return row[column - 1].strip()

    def pending_rows(self) -> list[PostRow]:
        values = self.worksheet.get_all_values()
        headers = values[HEADER_ROW - 1] if len(values) >= HEADER_ROW else []
        self.prepare(headers)
        columns = {
            name: self._column(name, optional=name == COL_TITLE)
            for name in [
                COL_PAGE_ID, COL_TITLE, COL_TEXT_CONTENT, COL_TELEGRAM_LINK,
                COL_VIDEO_ID, COL_POST_ID, COL_POST_LINK,
            ]
        }

        rows: list[PostRow] = []
        for row_number, row in enumerate(values[HEADER_ROW:], start=HEADER_ROW + 1):
            page_id = self._cell(row, columns[COL_PAGE_ID])
            text_content = self._cell(row, columns[COL_TEXT_CONTENT])
            telegram_link = self._cell(row, columns[COL_TELEGRAM_LINK])
            post_id = self._cell(row, columns[COL_POST_ID])
            post_link = self._cell(row, columns[COL_POST_LINK])

            if not any([page_id, text_content, telegram_link, post_link]):
                continue
            if post_link:
                if post_id:
                    continue
                if not page_id:
                    self.update(
                        row_number,
                        **{COL_STATUS: "Lỗi: thiếu PAGE_ID để lấy POST_ID từ Post Link"},
                    )
                    continue
            elif not all([page_id, text_content, telegram_link]):
                missing = [
                    name for name, value in [
                        (COL_PAGE_ID, page_id),
                        (COL_TEXT_CONTENT, text_content),
                        (COL_TELEGRAM_LINK, telegram_link),
                    ] if not value
                ]
                self.update(row_number, **{COL_STATUS: f"Lỗi: thiếu {', '.join(missing)}"})
                continue

            rows.append(PostRow(
                row_number=row_number,
                page_id=page_id,
                description=self._cell(row, columns[COL_TITLE]),
                text_content=text_content,
                telegram_link=telegram_link,
                video_id=self._cell(row, columns[COL_VIDEO_ID]),
                post_id=post_id,
                post_link=post_link,
            ))
        return rows

    def update(self, row_number: int, **values: str) -> None:
        updates = []
        for column_name, value in values.items():
            column = self._column(column_name)
            updates.append({
                "range": gspread.utils.rowcol_to_a1(row_number, column),
                "values": [[value]],
            })
        if updates:
            self.worksheet.batch_update(updates, value_input_option="RAW")
