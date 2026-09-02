"""
Upload ảnh/video và tạo Ad Creative.
Hỗ trợ 2 cách:
  1. Dùng lại 1 bài post có sẵn trên Fanpage (existing post) -> giống thao tác
     "Sử dụng bài viết hiện có" trên Ads Manager.
  2. Tạo creative mới từ ảnh + nội dung tự viết.
"""
import time

from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.adimage import AdImage
from facebook_business.adobjects.advideo import AdVideo


def upload_image(account: AdAccount, image_path: str) -> str:
    """Upload ảnh lên thư viện quảng cáo, trả về image_hash."""
    image = AdImage(parent_id=account.get_id())
    image[AdImage.Field.filename] = image_path
    image.remote_create()
    return image[AdImage.Field.hash]


def upload_video(account: AdAccount, video_path: str) -> str:
    """Upload video lên thư viện quảng cáo, trả về video_id."""
    video = AdVideo(parent_id=account.get_id())
    video[AdVideo.Field.filepath] = video_path
    video.remote_create()
    return video.get_id()


def wait_for_video_thumbnail(video_id: str, timeout_seconds: int = 600) -> str:
    """Chờ Meta xử lý video và trả về URL thumbnail dùng cho creative."""
    video = AdVideo(video_id)
    deadline = time.monotonic() + timeout_seconds
    last_status = ""
    while time.monotonic() < deadline:
        payload = video.api_get(fields=["status"])
        status = payload.get("status") or {}
        last_status = str(status)
        if isinstance(status, dict) and status.get("video_status") == "error":
            raise RuntimeError(f"Meta xử lý video quảng cáo thất bại: {status}")
        items = video.get_thumbnails(fields=["uri", "is_preferred"])
        if items:
            preferred = next(
                (item for item in items if item.get("is_preferred")), items[0]
            )
            url = preferred.get("uri")
            if url:
                return str(url)
        time.sleep(5)
    raise TimeoutError(
        "Hết thời gian chờ Meta tạo thumbnail cho video. "
        f"Trạng thái cuối: {last_status}"
    )


def create_creative_from_existing_post(
    account: AdAccount,
    page_id: str,
    post_id: str,
    name: str,
    call_to_action_type: str | None = None,
    page_welcome_message: str | None = None,
) -> AdCreative:
    """
    Tạo creative từ 1 bài post đã đăng sẵn trên Fanpage.
    post_id: chỉ phần số sau dấu "_" trong ID bài viết (không kèm page_id).
    call_to_action_type: gắn thêm 1 nút CTA lên bài viết khi chạy quảng cáo
                          (bài gốc không bị thay đổi). VD "MESSAGE_PAGE" để
                          thêm nút "Gửi tin nhắn" khi đích chuyển đổi là
                          Messenger. Để trống nếu muốn giữ nguyên bài viết,
                          không thêm nút CTA nào.
    """
    object_story_id = f"{page_id}_{post_id}"
    params = {
        AdCreative.Field.name: name,
        AdCreative.Field.object_story_id: object_story_id,
    }
    if call_to_action_type:
        params[AdCreative.Field.call_to_action_type] = call_to_action_type
    if page_welcome_message:
        params[AdCreative.Field.page_welcome_message] = page_welcome_message 

    return account.create_ad_creative(params=params)


def create_creative_from_image(
    account: AdAccount,
    page_id: str,
    message: str,
    link: str,
    image_hash: str,
    name: str,
    call_to_action_type: str = "SHOP_NOW",
) -> AdCreative:
    """Tạo creative mới (dạng link ad với 1 ảnh) thay vì dùng post có sẵn."""
    object_story_spec = {
        "page_id": page_id,
        "link_data": {
            "message": message,
            "link": link,
            "image_hash": image_hash,
            "call_to_action": {"type": call_to_action_type},
        },
    }
    params = {
        AdCreative.Field.name: name,
        AdCreative.Field.object_story_spec: object_story_spec,
    }
    return account.create_ad_creative(params=params)


def create_creative_from_video(
    account: AdAccount,
    page_id: str,
    message: str,
    video_id: str,
    thumbnail_url: str,
    name: str,
    call_to_action_type: str = "SHOP_NOW",
    link: str = "",
    cta_value: dict | None = None,
    title: str = "",
) -> AdCreative:
    """
    Tạo creative mới dạng video (dùng cho các mẫu '-VIDEO-AI' như trong tài khoản).

    cta_value: nếu cần tùy chỉnh nút CTA nâng cao (VD trỏ đến Messenger thay vì
               link web), truyền thẳng dict value, ví dụ:
               {"app_destination": "MESSENGER"}
               Nếu để trống, mặc định dùng {"link": link} như bình thường.
    """
    if cta_value is None:
        cta_value = {"link": link} if link else {}

    video_data = {
        "video_id": video_id,
        "message": message,
        "title": title,
        "image_url": thumbnail_url,
        "call_to_action": {
            "type": call_to_action_type,
            "value": cta_value,
        },
    }
    object_story_spec = {
        "page_id": page_id,
        "video_data": video_data,
    }
    params = {
        AdCreative.Field.name: name,
        AdCreative.Field.object_story_spec: object_story_spec,
    }
    return account.create_ad_creative(params=params)
