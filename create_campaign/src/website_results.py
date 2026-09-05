"""Save Website ad references; metadata reads never poll or retry."""
from __future__ import annotations

import json

from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.post import Post

from src.sheet_client import _build_header_map, _col_to_index


def read_post_once(creative_id: str) -> tuple[str, str]:
    """Read one creative, then its post once if Meta has returned a story ID."""
    try:
        creative = AdCreative(creative_id).api_get(fields=["effective_object_story_id"])
    except Exception as exc:  # Metadata failure must not recreate an ad.
        print(f"Creative {creative_id}: chưa đọc được POST_ID ({type(exc).__name__}); không thử lại")
        return "", ""
    story_id = str(creative.get("effective_object_story_id") or "")
    if not story_id:
        print(f"Creative {creative_id}: chưa có POST_ID; không thử lại")
        return "", ""
    post_id = story_id.rsplit("_", 1)[-1]
    try:
        post = Post(story_id).api_get(fields=["permalink_url"])
        link = str(post.get("permalink_url") or "")
        if link.startswith("/"):
            link = f"https://www.facebook.com{link}"
    except Exception as exc:
        print(f"Creative {creative_id}: chưa đọc được Post Link ({type(exc).__name__}); không thử lại")
        return post_id, ""
    if not link:
        print(f"Creative {creative_id}: chưa có Post Link; không thử lại")
    return post_id, link


class WebsiteResultWriter:
    """Write to the unique Mã row in Bài viết, using existing column headers."""

    def __init__(self, worksheet):
        self.worksheet = worksheet
        values = worksheet.get_all_values()
        headers = _build_header_map(values[0] if values else [])
        self.columns = {
            name: _col_to_index(headers, name) + 1
            for name in ["Mã", "FB_UPLOAD_ID", "POST_ID", "Post Link"]
        }
        self.rows = {}
        code_index = self.columns["Mã"] - 1
        for number, values_row in enumerate(values[1:], start=2):
            code = values_row[code_index].strip() if code_index < len(values_row) else ""
            if code:
                if code in self.rows:
                    raise ValueError(f"Mã '{code}' bị trùng trong tab Bài viết")
                self.rows[code] = number

    def _write(self, code: str, values: dict[str, str]) -> None:
        from gspread.utils import rowcol_to_a1

        if code not in self.rows:
            raise ValueError(f"Không tìm thấy Mã '{code}' trong tab Bài viết")
        self.worksheet.batch_update([
            {"range": rowcol_to_a1(self.rows[code], self.columns[name]), "values": [[value]]}
            for name, value in values.items()
        ], value_input_option="RAW")

    def write_upload(self, code: str, video_id: str) -> None:
        # Old post references belong to the previous upload, not this new video.
        self._write(code, {"FB_UPLOAD_ID": video_id, "POST_ID": "", "Post Link": ""})

    def write_posts(self, code: str, results: list[tuple[str, str]]) -> None:
        def cell(index: int) -> str:
            values = [result[index] for result in results]
            if not any(values):
                return ""
            if len(values) == 1:
                return values[0]
            return json.dumps([value or None for value in values], ensure_ascii=False)

        self._write(code, {"POST_ID": cell(0), "Post Link": cell(1)})
