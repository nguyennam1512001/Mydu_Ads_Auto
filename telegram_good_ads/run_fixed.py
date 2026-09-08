from __future__ import annotations

"""Compatibility entrypoint for workflow 11.

Meta Graph API v26 no longer supports the old aggregated ``attachments`` fields
used by the original fallback in ``run.py``.  This wrapper keeps all existing
workflow behaviour but replaces only ``resolve_video_id`` with a version that:

1. Reads direct video/reel IDs from the URL when available.
2. Reads the Page Post using ``id,object_id,type,permalink_url``.
3. Never requests the deprecated ``attachments`` field.

The rest of the logic (including matching FB_UPLOAD_ID from the ``Bài viết``
tab and writing by the ``Video id`` header) remains in ``run.py``.
"""

import run as base


def resolve_video_id(permalink: str, token: str) -> str:
    direct_video_id = base._video_id_from_url(permalink)
    if direct_video_id:
        return direct_video_id

    post_match = base.POST_URL_RE.search(permalink or "")
    if not post_match:
        return ""

    page_id = post_match.group("page")
    post_id = post_match.group("post")
    page_token = base.get_page_access_token(page_id, token)
    object_id = f"{page_id}_{post_id}"

    # Chỉ dùng các field hiện đại. Không gọi attachments vì Meta đã deprecate.
    payload = base.graph_get(
        object_id,
        "id,object_id,type,permalink_url",
        page_token,
    )

    if str(payload.get("type") or "").casefold() == "video":
        video_id = str(payload.get("object_id") or "").strip()
        if video_id:
            return video_id

    permalink_url = str(payload.get("permalink_url") or "")
    video_id = base._video_id_from_url(permalink_url)
    if video_id:
        return video_id

    return ""


# Các hàm trong module run.py tra resolve_video_id qua global của chính module,
# vì vậy thay thế tại đây áp dụng cho cả "Lấy Video ID" và lúc quét Telegram.
base.resolve_video_id = resolve_video_id


if __name__ == "__main__":
    base.main()
