from __future__ import annotations

"""Compatibility entrypoint for workflow 11.

Graph API v26 rejects legacy aggregated PagePost fields such as ``object_id``
and ``type`` on the PagePost node.  This wrapper keeps the existing workflow
logic but resolves Video ID using only current PagePost fields, then reads the
supported ``/attachments`` edge separately when media metadata is needed.
"""

import json

import run as base


def graph_get_edge(object_id: str, edge: str, fields: str, token: str) -> dict:
    params = base.urllib.parse.urlencode(
        {"fields": fields, "access_token": token}
    )
    quoted_id = base.urllib.parse.quote(object_id, safe="_")
    quoted_edge = base.urllib.parse.quote(edge.strip("/"), safe="")
    url = (
        f"https://graph.facebook.com/{base.GRAPH_VERSION}/"
        f"{quoted_id}/{quoted_edge}?{params}"
    )
    request = base.urllib.request.Request(
        url,
        headers={"User-Agent": "Mydu-Ads-Auto/1.0"},
    )
    try:
        with base.urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except base.urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Meta Graph API HTTP {exc.code}: {body[:500]}") from exc
    except base.urllib.error.URLError as exc:
        raise RuntimeError(f"Không kết nối được Meta Graph API: {exc}") from exc


def video_id_from_attachment_edge(node: object) -> str:
    if not isinstance(node, dict):
        return ""

    media_type = str(node.get("media_type") or node.get("type") or "").casefold()
    target = node.get("target")
    if "video" in media_type and isinstance(target, dict):
        video_id = str(target.get("id") or "").strip()
        if video_id:
            return video_id

    subattachments = node.get("subattachments")
    if isinstance(subattachments, dict):
        for child in subattachments.get("data") or []:
            video_id = video_id_from_attachment_edge(child)
            if video_id:
                return video_id

    return ""


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

    # PagePost v26: tránh object_id/type vì Meta coi chúng là legacy aggregated
    # attachment fields và trả (#12) deprecate_post_aggregated_fields_for_attachement.
    payload = base.graph_get(
        object_id,
        "id,permalink_url,status_type",
        page_token,
    )

    permalink_url = str(payload.get("permalink_url") or "")
    video_id = base._video_id_from_url(permalink_url)
    if video_id:
        return video_id

    # SDK hiện tại vẫn hỗ trợ /{page-post-id}/attachments. Đọc edge riêng thay
    # vì field expansion attachments{...} trên Post node.
    attachments = graph_get_edge(
        object_id,
        "attachments",
        "media_type,type,target,subattachments.limit(50){media_type,type,target}",
        page_token,
    )
    for attachment in attachments.get("data") or []:
        video_id = video_id_from_attachment_edge(attachment)
        if video_id:
            return video_id

    return ""


# Các hàm trong run.py tra resolve_video_id qua global của module run,
# nên monkey-patch này áp dụng cho cả "Lấy Video ID" và quét Telegram.
base.resolve_video_id = resolve_video_id


if __name__ == "__main__":
    base.main()
