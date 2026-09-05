from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests


@dataclass(frozen=True)
class PublishedPost:
    video_id: str
    post_id: str
    permalink_url: str


class FacebookPagePublisher:
    def __init__(self, access_token: str, graph_version: str = "v25.0") -> None:
        self.access_token = access_token
        self.base_url = f"https://graph.facebook.com/{graph_version}"
        self.http = requests.Session()
        self._page_tokens: dict[str, str] = {}

    @staticmethod
    def _raise_for_graph(response: requests.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Meta trả về dữ liệu không hợp lệ: HTTP {response.status_code}") from exc
        if not response.ok or "error" in payload:
            error = payload.get("error", {})
            message = error.get("message", response.text)
            code = error.get("code", response.status_code)
            raise RuntimeError(f"Meta API lỗi {code}: {message}")
        return payload

    def _page_token(self, page_id: str) -> str:
        cached = self._page_tokens.get(page_id)
        if cached:
            return cached

        response = self.http.get(
            f"{self.base_url}/{page_id}",
            params={"fields": "access_token", "access_token": self.access_token},
            timeout=60,
        )
        payload = self._raise_for_graph(response)
        page_token = payload.get("access_token") or self.access_token
        self._page_tokens[page_id] = page_token
        return page_token

    def upload_video(
        self,
        page_id: str,
        video_path: Path,
        message: str,
        *,
        title: str = "",
        message_button: bool = True,
    ) -> str:
        page_token = self._page_token(page_id)
        # Meta giới hạn tiêu đề video tối đa 255 ký tự. SP_Description có thể
        # dài hơn vì là mô tả sản phẩm, nên chỉ dùng 255 ký tự đầu làm title.
        safe_title = (title or "").strip()[:255]
        start = self.http.post(
            f"{self.base_url}/{page_id}/videos",
            data={
                "upload_phase": "start",
                "file_size": video_path.stat().st_size,
                "access_token": page_token,
            },
            timeout=60,
        )
        session = self._raise_for_graph(start)
        upload_session_id = session["upload_session_id"]
        video_id = str(session["video_id"])
        start_offset = int(session["start_offset"])
        end_offset = int(session["end_offset"])

        with video_path.open("rb") as video_file:
            while start_offset < end_offset:
                video_file.seek(start_offset)
                chunk = video_file.read(end_offset - start_offset)
                transfer = self.http.post(
                    f"{self.base_url}/{page_id}/videos",
                    data={
                        "upload_phase": "transfer",
                        "upload_session_id": upload_session_id,
                        "start_offset": start_offset,
                        "access_token": page_token,
                    },
                    files={"video_file_chunk": ("video.mp4", chunk)},
                    timeout=300,
                )
                offsets = self._raise_for_graph(transfer)
                new_start = int(offsets["start_offset"])
                end_offset = int(offsets["end_offset"])
                if new_start <= start_offset:
                    raise RuntimeError("Meta không cập nhật tiến độ upload video")
                start_offset = new_start

        finish_data = {
            "upload_phase": "finish",
            "upload_session_id": upload_session_id,
            "description": message,
            "title": safe_title,
            "published": "true",
            "access_token": page_token,
        }
        if message_button:
            call_to_action = {
                "type": "MESSAGE_PAGE",
                "value": {"link": f"https://m.me/{page_id}"},
            }
            finish_data["call_to_action"] = json.dumps(
                call_to_action,
                ensure_ascii=False,
            )
        finish = self.http.post(
            f"{self.base_url}/{page_id}/videos",
            data=finish_data,
            timeout=120,
        )
        self._raise_for_graph(finish)
        return video_id

    def wait_for_post(self, page_id: str, video_id: str, timeout_seconds: int = 900) -> PublishedPost:
        page_token = self._page_token(page_id)
        deadline = time.monotonic() + timeout_seconds
        last_status = ""
        while time.monotonic() < deadline:
            response = self.http.get(
                f"{self.base_url}/{video_id}",
                params={
                    "fields": "id,post_id,permalink_url,status",
                    "access_token": page_token,
                },
                timeout=60,
            )
            payload = self._raise_for_graph(response)
            status = payload.get("status") or {}
            last_status = str(status)
            video_status = status.get("video_status") if isinstance(status, dict) else ""
            if video_status == "error":
                raise RuntimeError(f"Meta xử lý video thất bại: {status}")

            post_id = str(payload.get("post_id") or "")
            permalink = str(payload.get("permalink_url") or "")
            if permalink.startswith("/"):
                permalink = f"https://www.facebook.com{permalink}"
            if post_id and permalink:
                if "_" in post_id:
                    post_id = post_id.rsplit("_", 1)[-1]
                return PublishedPost(video_id=video_id, post_id=post_id, permalink_url=permalink)
            time.sleep(5)

        raise TimeoutError(
            "Hết thời gian chờ Meta tạo POST_ID hoặc Post Link. "
            f"Trạng thái cuối: {last_status}"
        )


    @staticmethod
    def _video_id_from_permalink(permalink_url: str) -> str:
        parsed = urlparse((permalink_url or "").strip())
        query = parse_qs(parsed.query)
        watch_id = query.get("v", [""])[0]
        if parsed.path.rstrip("/") in {"/watch", "/video.php"} and watch_id.isdigit():
            return watch_id
        path = parsed.path if parsed.scheme else permalink_url
        parts = [part for part in path.split("/") if part]
        for marker in ("reel", "videos"):
            if marker in parts:
                index = parts.index(marker) + 1
                if index < len(parts) and parts[index].isdigit():
                    return parts[index]
        raise ValueError(
            "Post Link phải có dạng facebook.com/reel/ID hoặc facebook.com/.../videos/ID"
        )

    def recover_video_reference(self, page_id: str, post_id: str, post_link: str) -> PublishedPost:
        """Resolve an existing video reference without publishing anything."""
        if post_link:
            try:
                video_id = self._video_id_from_permalink(post_link)
                return PublishedPost(video_id, post_id, post_link)
            except ValueError:
                pass
        story_id = post_id
        if not story_id and post_link:
            parsed = urlparse(post_link)
            story_id = parse_qs(parsed.query).get("story_fbid", [""])[0]
            parts = [part for part in parsed.path.split("/") if part]
            if not story_id and "posts" in parts:
                index = parts.index("posts") + 1
                if index < len(parts):
                    story_id = parts[index]
        if not story_id:
            raise ValueError("Không xác định được video/bài viết từ Post Link; cần POST_ID hoặc FB_UPLOAD_ID")
        if "_" not in story_id:
            story_id = f"{page_id}_{story_id}"
        response = self.http.get(
            f"{self.base_url}/{story_id}",
            params={
                "fields": "id,object_id,type,permalink_url",
                "access_token": self._page_token(page_id),
            },
            timeout=60,
        )
        payload = self._raise_for_graph(response)
        video_id = str(payload.get("object_id") or "") if payload.get("type") == "video" else ""
        link = post_link or str(payload.get("permalink_url") or "")
        if link.startswith("/"):
            link = f"https://www.facebook.com{link}"
        if not video_id and link:
            try:
                video_id = self._video_id_from_permalink(link)
            except ValueError:
                pass
        return PublishedPost(
            video_id, post_id or str(payload.get("id") or story_id).rsplit("_", 1)[-1], link,
        )

    def recover_post_id(
        self,
        page_id: str,
        permalink_url: str,
        timeout_seconds: int = 300,
    ) -> str:
        video_id = self._video_id_from_permalink(permalink_url)
        page_token = self._page_token(page_id)
        deadline = time.monotonic() + timeout_seconds
        last_status = ""
        while time.monotonic() < deadline:
            response = self.http.get(
                f"{self.base_url}/{video_id}",
                params={
                    "fields": "id,post_id,status",
                    "access_token": page_token,
                },
                timeout=60,
            )
            payload = self._raise_for_graph(response)
            status = payload.get("status") or {}
            last_status = str(status)
            post_id = str(payload.get("post_id") or "")
            if post_id:
                return post_id.rsplit("_", 1)[-1]
            time.sleep(5)

        raise TimeoutError(
            "Hết thời gian lấy POST_ID từ Post Link. "
            f"Trạng thái cuối: {last_status}"
        )
