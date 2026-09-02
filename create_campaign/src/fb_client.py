"""
Khởi tạo kết nối tới Facebook Marketing API.
Đọc thông tin xác thực từ biến môi trường (file .env).
"""
import os

from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount


def init_api() -> None:
    """Khởi tạo FacebookAdsApi bằng App ID / App Secret / Access Token."""
    app_id = os.getenv("FB_APP_ID")
    app_secret = os.getenv("FB_APP_SECRET")
    access_token = os.getenv("FB_ACCESS_TOKEN")

    missing = [
        name
        for name, val in [
            ("FB_APP_ID", app_id),
            ("FB_APP_SECRET", app_secret),
            ("FB_ACCESS_TOKEN", access_token),
        ]
        if not val
    ]
    if missing:
        raise EnvironmentError(
            f"Thiếu biến môi trường: {', '.join(missing)}. "
            f"Kiểm tra lại file .env (xem README.md phần 'Lấy thông tin API')."
        )

    FacebookAdsApi.init(app_id, app_secret, access_token)


def get_ad_account(account_id: str | None = None) -> AdAccount:
    """
    Trả về đối tượng AdAccount.

    account_id: nếu truyền vào (VD: đọc từ 1 dòng Google Sheet, mỗi dòng có thể
                thuộc 1 Ad Account khác nhau) thì dùng giá trị này. Nếu để trống,
                lấy mặc định từ FB_AD_ACCOUNT_ID trong .env (dùng cho chế độ chạy
                từ 1 file YAML cố định).
    Access Token trong .env (System User) cần có quyền Manage campaigns trên
    TẤT CẢ các Ad Account sẽ dùng, kể cả khi chạy nhiều tài khoản khác nhau
    từ Google Sheet.
    """
    account_id = account_id or os.getenv("FB_AD_ACCOUNT_ID")
    if not account_id:
        raise EnvironmentError(
            "Thiếu Ad Account ID: không có trong .env (FB_AD_ACCOUNT_ID) và "
            "cũng không được truyền vào."
        )

    if not account_id.startswith("act_"):
        account_id = f"act_{account_id}"

    return AdAccount(account_id)
