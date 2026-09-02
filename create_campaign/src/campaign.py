"""Tạo Chiến dịch (Campaign) trên Facebook Ads."""
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign


def create_campaign(
    account: AdAccount,
    name: str,
    objective: str = "OUTCOME_ENGAGEMENT",
    status: str = "PAUSED",
    special_ad_categories: list | None = None,
    daily_budget: int | None = None,
    bid_strategy: str | None = None,
    bid_amount: int | None = None,
) -> Campaign:
    """
    Tạo 1 campaign mới.

    objective: OUTCOME_ENGAGEMENT, OUTCOME_TRAFFIC, OUTCOME_SALES,
               OUTCOME_LEADS, OUTCOME_AWARENESS, OUTCOME_APP_PROMOTION ...
    status: PAUSED (khuyến nghị khi test) hoặc ACTIVE
    daily_budget: ngân sách ngày ở CẤP CAMPAIGN (CBO). Nếu để ngân sách ở
                  cấp AdSet thì bỏ qua tham số này.
    bid_strategy: CHỈ cần khi dùng daily_budget ở đây (CBO) — chiến lược giá
                  thầu phải khai báo ở Campaign, không phải AdSet, nếu không
                  Facebook sẽ báo lỗi "phải nhập giá thầu". Mặc định Facebook
                  tự chọn LOWEST_COST_WITHOUT_CAP nếu để trống.
    bid_amount: chỉ cần khi bid_strategy là LOWEST_COST_WITH_BID_CAP/COST_CAP.
    """
    params = {
        Campaign.Field.name: name,
        Campaign.Field.objective: objective,
        Campaign.Field.status: status,
        Campaign.Field.special_ad_categories: special_ad_categories or [],
        # Đây là tính năng "chia sẻ ngân sách linh hoạt giữa AdSet" (khác CBO),
        # xung đột với việc đặt ngân sách cố định ở cấp Campaign -> luôn để False.
        "is_adset_budget_sharing_enabled": False,
    }
    if daily_budget:
        params[Campaign.Field.daily_budget] = daily_budget
        if bid_strategy:
            params[Campaign.Field.bid_strategy] = bid_strategy
        if bid_amount:
            params[Campaign.Field.bid_amount] = bid_amount

    return account.create_campaign(params=params)
