from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from html.parser import HTMLParser

import gspread
import requests
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
HEADER_ROW = 1
COL_CODE = "Mã"
COL_AD_ACCOUNT_ID = "AD_ACCOUNT_ID"
COL_PAGE_ID = "PAGE_ID"
COL_VIDEO_ID = "FB_UPLOAD_ID"
COL_POST_LINK = "Post Link"
COL_IMAGE_HASH = "Image Hash"
SOURCE_FB_UPLOAD_ID = "fb_upload_id"
SOURCE_THUMBDOWNLOADER = "thumbdownloader"
THUMBDOWNLOADER_URL = "https://www.thumbdownloader.com/"


def normalize_header(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(f"Thiếu biến môi trường: {name}")
    return value


def header_map(headers: list[str]) -> dict[str, int]:
    return {
        normalize_header(value): index
        for index, value in enumerate(headers)
        if normalize_header(value)
    }


def column(columns: dict[str, int], name: str) -> int:
    key = normalize_header(name)
    if key not in columns:
        raise ValueError(f"Không tìm thấy cột '{name}'")
    return columns[key]


def cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


@dataclass(frozen=True)
class ImageHashRow:
    row_number: int
    code: str
    ad_account_id: str
    page_id: str
    video_id: str
    post_link: str


class HighestQualityThumbnailParser(HTMLParser):
    """Read only the first direct download URL labelled Highest quality thumbnail."""

    def __init__(self) -> None:
        super().__init__()
        self._item_depth: int | None = None
        self._div_depth = 0
        self._item_text: list[str] = []
        self._item_download_url = ""
        self.thumbnail_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "div":
            self._div_depth += 1
            if "itemwrap" in classes and self._item_depth is None:
                self._item_depth = self._div_depth
                self._item_text = []
                self._item_download_url = ""
        if self._item_depth is not None and tag == "a":
            if {"btn", "volatile"}.issubset(classes) and attributes.get("href"):
                self._item_download_url = str(attributes["href"])

    def handle_data(self, data: str) -> None:
        if self._item_depth is not None:
            self._item_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            if self._item_depth == self._div_depth:
                label = " ".join(self._item_text).casefold()
                if "highest quality thumbnail" in label and self._item_download_url:
                    self.thumbnail_url = self._item_download_url
                self._item_depth = None
            self._div_depth -= 1


def read_rows(
    spreadsheet: gspread.Spreadsheet, *, source: str
) -> tuple[gspread.Worksheet, list[ImageHashRow]]:
    posts = spreadsheet.worksheet("Bài viết")
    campaigns = spreadsheet.worksheet("Lên Camp")

    campaign_values = campaigns.get_all_values()
    if not campaign_values:
        raise ValueError("Tab 'Lên Camp' không có dữ liệu")
    campaign_columns = header_map(campaign_values[HEADER_ROW - 1])
    campaign_code = column(campaign_columns, COL_CODE)
    campaign_account = column(campaign_columns, COL_AD_ACCOUNT_ID)

    accounts_by_code: dict[str, set[str]] = {}
    for row in campaign_values[HEADER_ROW:]:
        code = cell(row, campaign_code)
        account_id = cell(row, campaign_account).removeprefix("act_")
        if code and account_id:
            accounts_by_code.setdefault(code, set()).add(account_id)

    post_values = posts.get_all_values()
    if not post_values:
        return posts, []
    post_columns = header_map(post_values[HEADER_ROW - 1])
    post_code = column(post_columns, COL_CODE)
    post_page = column(post_columns, COL_PAGE_ID) if source == SOURCE_FB_UPLOAD_ID else None
    post_video = column(post_columns, COL_VIDEO_ID) if source == SOURCE_FB_UPLOAD_ID else None
    post_link_column = (
        column(post_columns, COL_POST_LINK) if source == SOURCE_THUMBDOWNLOADER else None
    )
    post_hash = column(post_columns, COL_IMAGE_HASH)

    rows: list[ImageHashRow] = []
    errors: list[str] = []
    for row_number, row in enumerate(post_values[HEADER_ROW:], start=HEADER_ROW + 1):
        code = cell(row, post_code)
        page_id = cell(row, post_page) if post_page is not None else ""
        video_id = cell(row, post_video) if post_video is not None else ""
        post_link = cell(row, post_link_column) if post_link_column is not None else ""
        image_hash = cell(row, post_hash)
        source_value = video_id if source == SOURCE_FB_UPLOAD_ID else post_link
        if image_hash or not source_value:
            continue
        if not code:
            errors.append(f"Dòng {row_number}: thiếu Mã")
            continue
        if source == SOURCE_FB_UPLOAD_ID and not page_id:
            errors.append(f"Dòng {row_number}: thiếu PAGE_ID")
            continue
        account_ids = accounts_by_code.get(code, set())
        if not account_ids:
            errors.append(
                f"Dòng {row_number}: không tìm thấy AD_ACCOUNT_ID cho Mã '{code}' ở tab 'Lên Camp'"
            )
            continue
        if len(account_ids) > 1:
            errors.append(
                f"Dòng {row_number}: Mã '{code}' có nhiều AD_ACCOUNT_ID: "
                f"{', '.join(sorted(account_ids))}"
            )
            continue
        rows.append(ImageHashRow(
            row_number=row_number,
            code=code,
            ad_account_id=next(iter(account_ids)),
            page_id=page_id,
            video_id=video_id,
            post_link=post_link,
        ))
    if errors:
        raise ValueError("; ".join(errors))
    return posts, rows


class MetaImageHashClient:
    def __init__(self, access_token: str, graph_version: str) -> None:
        self.access_token = access_token
        self.base_url = f"https://graph.facebook.com/{graph_version}"
        self.http = requests.Session()
        self._page_tokens: dict[str, str] = {}

    @staticmethod
    def _payload(response: requests.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Meta trả dữ liệu không hợp lệ: HTTP {response.status_code}"
            ) from exc
        if not response.ok or "error" in payload:
            error = payload.get("error", {})
            raise RuntimeError(
                f"Meta API lỗi {error.get('code', response.status_code)}: "
                f"{error.get('message', response.text)}"
            )
        return payload

    def page_token(self, page_id: str) -> str:
        if page_id not in self._page_tokens:
            response = self.http.get(
                f"{self.base_url}/{page_id}",
                params={"fields": "access_token", "access_token": self.access_token},
                timeout=60,
            )
            self._page_tokens[page_id] = str(
                self._payload(response).get("access_token") or self.access_token
            )
        return self._page_tokens[page_id]

    def thumbnail_url(self, page_id: str, video_id: str) -> str:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            response = self.http.get(
                f"{self.base_url}/{video_id}/thumbnails",
                params={
                    "fields": "uri,is_preferred",
                    "access_token": self.page_token(page_id),
                },
                timeout=60,
            )
            items = self._payload(response).get("data") or []
            if items:
                preferred = next(
                    (item for item in items if item.get("is_preferred")), items[0]
                )
                if preferred.get("uri"):
                    return str(preferred["uri"])
            time.sleep(5)
        raise TimeoutError(f"Hết thời gian chờ thumbnail của FB_UPLOAD_ID {video_id}")

    def thumbdownloader_thumbnail_url(self, source_url: str) -> str:
        """Ask ThumbDownloader for its direct Highest quality thumbnail download URL."""
        response = self.http.get(
            THUMBDOWNLOADER_URL,
            params={"u": source_url},
            timeout=120,
        )
        response.raise_for_status()
        parser = HighestQualityThumbnailParser()
        parser.feed(response.text)
        if not parser.thumbnail_url:
            raise RuntimeError(
                "ThumbDownloader không trả về 'Highest quality thumbnail' cho URL này"
            )
        return parser.thumbnail_url

    def create_image_hash(self, ad_account_id: str, thumbnail_url: str) -> str:
        image_response = self.http.get(thumbnail_url, timeout=120)
        image_response.raise_for_status()
        response = self.http.post(
            f"{self.base_url}/act_{ad_account_id}/adimages",
            data={"access_token": self.access_token},
            files={
                "filename": (
                    "video-thumbnail.jpg",
                    image_response.content,
                    image_response.headers.get("content-type", "image/jpeg"),
                )
            },
            timeout=180,
        )
        images = self._payload(response).get("images") or {}
        for image in images.values():
            if image.get("hash"):
                return f"{ad_account_id}:{image['hash']}"
        raise RuntimeError("Meta upload ảnh thành công nhưng không trả về Image Hash")


def run(*, limit: int | None = None, source: str = SOURCE_FB_UPLOAD_ID) -> None:
    load_dotenv()
    credentials = json.loads(required_env("GOOGLE_CREDENTIALS"))
    client = gspread.authorize(
        Credentials.from_service_account_info(credentials, scopes=SCOPES)
    )
    spreadsheet = client.open_by_key(required_env("GOOGLE_SHEET_ID"))
    worksheet, rows = read_rows(spreadsheet, source=source)
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        print("Không có dòng nào cần lấy Image Hash.")
        return

    columns = header_map(worksheet.row_values(HEADER_ROW))
    hash_column = column(columns, COL_IMAGE_HASH) + 1
    meta = MetaImageHashClient(
        required_env("FB_ACCESS_TOKEN"),
        os.getenv("FB_GRAPH_VERSION", "v25.0"),
    )
    failures = 0
    for row in rows:
        started = time.monotonic()
        try:
            url = (
                meta.thumbnail_url(row.page_id, row.video_id)
                if source == SOURCE_FB_UPLOAD_ID
                else meta.thumbdownloader_thumbnail_url(row.post_link)
            )
            value = meta.create_image_hash(row.ad_account_id, url)
            worksheet.update_acell(
                gspread.utils.rowcol_to_a1(row.row_number, hash_column), value
            )
            print(f"Dòng {row.row_number}: {value} ({time.monotonic() - started:.1f}s)")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"Dòng {row.row_number}: Lỗi Image Hash: {exc}")
    if failures:
        raise RuntimeError(f"Có {failures} dòng lấy Image Hash thất bại")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lấy Image Hash từ FB_UPLOAD_ID hoặc Post Link")
    parser.add_argument("--limit", type=int, help="Giới hạn số dòng xử lý")
    parser.add_argument(
        "--source",
        choices=[SOURCE_FB_UPLOAD_ID, SOURCE_THUMBDOWNLOADER],
        default=SOURCE_FB_UPLOAD_ID,
        help="Nguồn thumbnail: fb_upload_id (mặc định) hoặc thumbdownloader (cột Post Link)",
    )
    args = parser.parse_args()
    run(limit=args.limit, source=args.source)


if __name__ == "__main__":
    main()
