"""Tạo Nhóm quảng cáo (Ad Set) trên Facebook Ads."""
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adset import AdSet


def create_adset(
    account: AdAccount,
    name: str,
    campaign_id: str,
    billing_event: str,
    optimization_goal: str,
    targeting: dict,
    daily_budget: int | None = None,
    status: str = "PAUSED",
    start_time: str | None = None,
    bid_strategy: str | None = None,
    bid_amount: int | None = None,
    promoted_object: dict | None = None,
    destination_type: str | None = None,
) -> AdSet:
    """
    Tạo 1 ad set mới trong campaign đã cho.

    daily_budget: CHỈ truyền vào khi KHÔNG dùng ngân sách cấp Campaign (CBO).
                  Nếu Campaign đã đặt ngân sách (is_adset_budget_sharing_enabled),
                  để trống (None) — Facebook không cho AdSet có ngân sách riêng
                  khi Campaign đã quản lý ngân sách chung.
                  Đơn vị: tiền tệ nhỏ nhất của tài khoản (VND không có phần thập
                  phân, ví dụ 100000 = 100.000đ).
    billing_event: IMPRESSIONS, LINK_CLICKS, ...
    optimization_goal: POST_ENGAGEMENT, LINK_CLICKS, OFFSITE_CONVERSIONS,
                        REACH, VIDEO_VIEWS, CONVERSATIONS (khi đích là Tin nhắn), ...
    bid_strategy: CHỈ truyền khi KHÔNG dùng CBO (ngân sách cấp AdSet). Nếu dùng
                  CBO thì bid_strategy phải khai báo ở create_campaign(), để
                  trống ở đây — nếu không sẽ bị lỗi "phải nhập giá thầu".
                  LOWEST_COST_WITHOUT_CAP (mặc định, để Facebook tự tối ưu giá),
                  LOWEST_COST_WITH_BID_CAP (phải kèm bid_amount = giá thầu tối đa),
                  COST_CAP (phải kèm bid_amount = mức chi phí mục tiêu).
    bid_amount: chỉ cần khi bid_strategy khác LOWEST_COST_WITHOUT_CAP.
    promoted_object: bắt buộc với optimization_goal = POST_ENGAGEMENT/PAGE_LIKES/
                      CONVERSATIONS, ví dụ {"page_id": "1294066237122353"}.
    destination_type: "MESSENGER" khi Vị trí chuyển đổi = Tin nhắn (Messenger),
                       "WHATSAPP" cho WhatsApp, "INSTAGRAM_DIRECT" cho Instagram
                       Direct. Để trống nếu đích chuyển đổi vẫn là Website.
    targeting: dict theo cấu trúc Targeting của Facebook, ví dụ:
        {
          "geo_locations": {"countries": ["VN"]},
          "age_min": 18,
          "age_max": 45
        }
        Nếu không tự khai báo "targeting_automation", mặc định sẽ TẮT
        "Đối tượng Advantage" (advantage_audience: 0) để giữ đúng đối tượng
        bạn nhắm tới, không để Facebook tự mở rộng.
    """
    targeting = dict(targeting)  # tránh sửa trực tiếp dict gốc từ config
    targeting.setdefault("targeting_automation", {"advantage_audience": 0})

    params = {
        AdSet.Field.name: name,
        AdSet.Field.campaign_id: campaign_id,
        AdSet.Field.billing_event: billing_event,
        AdSet.Field.optimization_goal: optimization_goal,
        AdSet.Field.targeting: targeting,
        AdSet.Field.status: status,
    }
    if daily_budget:
        params[AdSet.Field.daily_budget] = daily_budget
    if bid_strategy:
        params[AdSet.Field.bid_strategy] = bid_strategy
    if start_time:
        params[AdSet.Field.start_time] = start_time
    if bid_amount:
        params[AdSet.Field.bid_amount] = bid_amount
    if promoted_object:
        params[AdSet.Field.promoted_object] = promoted_object
    if destination_type:
        params[AdSet.Field.destination_type] = destination_type

    return account.create_ad_set(params=params)
