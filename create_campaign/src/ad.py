"""Tạo Quảng cáo (Ad) trên Facebook Ads."""
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.adaccount import AdAccount


def create_ad(
    account: AdAccount,
    name: str,
    adset_id: str,
    creative_id: str,
    status: str = "PAUSED",
) -> Ad:
    """Tạo 1 ad mới, gắn vào adset_id và dùng creative_id đã tạo trước đó."""
    params = {
        Ad.Field.name: name,
        Ad.Field.adset_id: adset_id,
        Ad.Field.creative: {"creative_id": creative_id},
        Ad.Field.status: status,
    }
    return account.create_ad(params=params)
