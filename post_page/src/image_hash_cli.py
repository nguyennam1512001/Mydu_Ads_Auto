from __future__ import annotations

import argparse
import io
import json
import os
import re
import time
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse

import gspread
import requests
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


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
COL_LINK = "link"
COL_PREVIEW = "Preview"
GOOD_ADS_TAB = "Các bài ads chạy tốt <150k"
SOURCE_FB_UPLOAD_ID = "fb_upload_id"
SOURCE_THUMBDOWNLOADER = "thumbdownloader"
THUMBDOWNLOADER_URL = "https://www.thumbdownloader.com/"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


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


def image_filename(source_url: str) -> str:
    """Make a stable filename from a URL without requiring a Facebook URL."""
    parts = [part for part in urlparse(source_url).path.split("/") if part]
    numeric_parts = [part for part in parts if part.isdecimal()]
    if len(numeric_parts) >= 2:
        stem = "_".join(numeric_parts[-2:])
    else:
        stem = re.sub(r"[^A-Za-z0-9]+", "_", unquote(source_url)).strip("_")
    if not stem:
        raise ValueError("Không thể đặt tên ảnh từ Post Link trống")
    return f"{stem[:180]}.jpg"


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
        """Ask ThumbDownloader for its direct Highest quality thumbnail download URL.

        The source URL is deliberately passed through unchanged: this mode does not
        validate host, path, or whether the post is public.
        """
        response = self.http.get(
            THUMBDOWNLOADER_URL,
            params={"u": source_url},
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            },
            timeout=120,
        )
        response.raise_for_status()
        parser = HighestQualityThumbnailParser()
        parser.feed(response.text)
        if not parser.thumbnail_url:
            # The site has changed attribute order before; retain a narrow fallback
            # for its Download button rather than selecting the thumbnail sprite.
            match = re.search(
                r'<a[^>]*class=["\'][^"\']*\bbtn\b[^"\']*\bvolatile\b[^"\']*["\'][^>]*'
                r'href=["\']([^"\']+)["\']',
                response.text,
                flags=re.IGNORECASE,
            )
            if match:
                parser.thumbnail_url = unescape(match.group(1))
        if not parser.thumbnail_url:
            title = re.search(r"<title[^>]*>(.*?)</title>", response.text, flags=re.IGNORECASE | re.DOTALL)
            page_title = re.sub(r"\s+", " ", unescape(title.group(1))).strip() if title else "không có title"
            raise RuntimeError(
                "ThumbDownloader không trả về 'Highest quality thumbnail' "
                f"(HTTP {response.status_code}, title: {page_title[:120]}, URL phản hồi: {response.url})"
            )
        return parser.thumbnail_url

    def download_thumbnail(self, thumbnail_url: str) -> requests.Response:
        image_response = self.http.get(thumbnail_url, timeout=120)
        image_response.raise_for_status()
        return image_response

    def create_image_hash(self, ad_account_id: str, image_response: requests.Response) -> str:
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


class DrivePreviewWriter:
    def __init__(self, spreadsheet: gspread.Spreadsheet) -> None:
        oauth_info = json.loads(required_env("OAuth_Google"))
        required_fields = ("client_id", "client_secret", "refresh_token")
        missing = [field for field in required_fields if not str(oauth_info.get(field, "")).strip()]
        if missing:
            raise ValueError("OAuth_Google thiếu trường bắt buộc: " + ", ".join(missing))
        credentials = UserCredentials(
            token=None,
            refresh_token=str(oauth_info["refresh_token"]).strip(),
            token_uri=str(oauth_info.get("token_uri") or "https://oauth2.googleapis.com/token").strip(),
            client_id=str(oauth_info["client_id"]).strip(),
            client_secret=str(oauth_info["client_secret"]).strip(),
            scopes=DRIVE_SCOPES,
        )
        self.drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self.folder_id = required_env("GOOGLE_DRIVE_PREVIEW_FOLDER_ID")
        self.worksheet = spreadsheet.worksheet(GOOD_ADS_TAB)
        columns = header_map(self.worksheet.row_values(HEADER_ROW))
        self.link_column = column(columns, COL_LINK) + 1
        self.preview_column = column(columns, COL_PREVIEW) + 1

    @staticmethod
    def drive_image_url(file_id: str) -> str:
        return f"https://drive.usercontent.google.com/download?id={file_id}&export=view&authuser=0"

    def _find_file(self, filename: str) -> str | None:
        safe_name = filename.replace("'", "\\'")
        result = self.drive.files().list(
            q=f"name = '{safe_name}' and '{self.folder_id}' in parents and trashed = false",
            spaces="drive",
            fields="files(id,name)",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = result.get("files", [])
        return str(files[0]["id"]) if files else None

    def _ensure_public_reader(self, file_id: str) -> None:
        permissions = self.drive.permissions().list(
            fileId=file_id,
            fields="permissions(type,role)",
            supportsAllDrives=True,
        ).execute().get("permissions", [])
        if not any(p.get("type") == "anyone" and p.get("role") == "reader" for p in permissions):
            self.drive.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
                supportsAllDrives=True,
            ).execute()

    def upload_image(self, source_url: str, image_response: requests.Response) -> str:
        filename = image_filename(source_url)
        file_id = self._find_file(filename)
        if not file_id:
            media = MediaIoBaseUpload(
                io.BytesIO(image_response.content),
                mimetype=image_response.headers.get("content-type", "image/jpeg"),
                resumable=False,
            )
            created = self.drive.files().create(
                body={"name": filename, "parents": [self.folder_id]},
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            ).execute()
            file_id = str(created["id"])
        self._ensure_public_reader(file_id)
        return file_id

    def write_preview(self, source_url: str, file_id: str) -> int:
        rows = self.worksheet.get_all_values(value_render_option="FORMULA")
        matches = [
            row_number
            for row_number, row in enumerate(rows[HEADER_ROW:], start=HEADER_ROW + 1)
            if cell(row, self.link_column - 1) == source_url
        ]
        if not matches:
            raise ValueError(f"Không có dòng cột '{COL_LINK}' khớp Post Link: {source_url}")
        formula = f'=IMAGE("{self.drive_image_url(file_id)}")'
        self.worksheet.batch_update(
            [
                {"range": gspread.utils.rowcol_to_a1(row_number, self.preview_column), "values": [[formula]]}
                for row_number in matches
            ],
            value_input_option="USER_ENTERED",
        )
        return len(matches)


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
    preview_writer = DrivePreviewWriter(spreadsheet) if source == SOURCE_THUMBDOWNLOADER else None
    failures = 0
    for row in rows:
        started = time.monotonic()
        try:
            url = (
                meta.thumbnail_url(row.page_id, row.video_id)
                if source == SOURCE_FB_UPLOAD_ID
                else meta.thumbdownloader_thumbnail_url(row.post_link)
            )
            image_response = meta.download_thumbnail(url)
            value = meta.create_image_hash(row.ad_account_id, image_response)
            worksheet.update_acell(
                gspread.utils.rowcol_to_a1(row.row_number, hash_column), value
            )
            preview_note = ""
            if preview_writer:
                file_id = preview_writer.upload_image(row.post_link, image_response)
                match_count = preview_writer.write_preview(row.post_link, file_id)
                preview_note = f"; Drive + Preview {match_count} dòng"
            print(f"Dòng {row.row_number}: {value}{preview_note} ({time.monotonic() - started:.1f}s)")
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
