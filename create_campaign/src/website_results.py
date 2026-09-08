"""Lưu tham chiếu bài viết của quảng cáo sau khi Meta hoàn tất tạo post."""
from __future__ import annotations

import json
import time

from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.post import Post

from src.sheet_client import _build_header_map, _col_to_index

POST_LOOKUP_ATTEMPTS = 6
POST_LOOKUP_DELAY_SECONDS = 5


def read_post_once(creative_id: str) -> tuple[str, str]:
    """Đợi ngắn để Meta trả POST_ID rồi đọc permalink của bài viết một lần có retry."""
    story_id = ""
    for attempt in range(1, POST_LOOKUP_ATTEMPTS + 1):
        try:
            creative = AdCreative(creative_id).api_get(fields=["effective_object_story_id"])
            story_id = str(creative.get("effective_object_story_id") or "")
        except Exception as exc:  # Metadata failure must not recreate an ad.
            print(f"Creative {creative_id}: chưa đọc được POST_ID ({type(exc).__name__})")
        if story_id:
            break
        if attempt < POST_LOOKUP_ATTEMPTS:
            print(
                f"Creative {creative_id}: chưa có POST_ID; thử lại sau "
                f"{POST_LOOKUP_DELAY_SECONDS}s ({attempt}/{POST_LOOKUP_ATTEMPTS})"
            )
            time.sleep(POST_LOOKUP_DELAY_SECONDS)
    if not story_id:
        print(f"Creative {creative_id}: hết thời gian chờ POST_ID")
        return "", ""

    post_id = story_id.rsplit("_", 1)[-1]
    for attempt in range(1, POST_LOOKUP_ATTEMPTS + 1):
        try:
            post = Post(story_id).api_get(fields=["permalink_url"])
            link = str(post.get("permalink_url") or "")
            if link.startswith("/"):
                link = f"https://www.facebook.com{link}"
            if link:
                return post_id, link
        except Exception as exc:
            print(f"Creative {creative_id}: chưa đọc được Post Link ({type(exc).__name__})")
        if attempt < POST_LOOKUP_ATTEMPTS:
            print(
                f"Creative {creative_id}: chưa có Post Link; thử lại sau "
                f"{POST_LOOKUP_DELAY_SECONDS}s ({attempt}/{POST_LOOKUP_ATTEMPTS})"
            )
            time.sleep(POST_LOOKUP_DELAY_SECONDS)
    print(f"Creative {creative_id}: hết thời gian chờ Post Link")
    return post_id, ""


class WebsiteResultWriter:
    """Ghi kết quả Website vào đúng số hàng tương ứng trong tab Bài viết."""

    def __init__(self, worksheet):
        self.worksheet = worksheet
        values = worksheet.get_all_values()
        headers = _build_header_map(values[0] if values else [])
        self.columns = {
            name: _col_to_index(headers, name) + 1
            for name in ["FB_UPLOAD_ID", "POST_ID", "Post Link"]
        }

    def _write(self, row_number: int, values: dict[str, str]) -> None:
        from gspread.utils import rowcol_to_a1

        if row_number < 2:
            raise ValueError(f"Số hàng không hợp lệ trong tab Bài viết: {row_number}")
        self.worksheet.batch_update([
            {"range": rowcol_to_a1(row_number, self.columns[name]), "values": [[value]]}
            for name, value in values.items()
        ], value_input_option="RAW")

    def write_upload(self, row_number: int, video_id: str) -> None:
        # Old post references belong to the previous upload, not this new video.
        self._write(row_number, {"FB_UPLOAD_ID": video_id, "POST_ID": "", "Post Link": ""})

    def write_posts(self, row_number: int, results: list[tuple[str, str]]) -> None:
        def cell(index: int) -> str:
            values = [result[index] for result in results]
            if not any(values):
                return ""
            if len(values) == 1:
                return values[0]
            return json.dumps([value or None for value in values], ensure_ascii=False)

        self._write(row_number, {"POST_ID": cell(0), "Post Link": cell(1)})
