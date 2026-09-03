"""
CLI để tự động tạo Campaign + AdSet + Ad trên Facebook Ads Manager
dựa theo 1 file cấu hình YAML.

Cách chạy:
    python run.py config/campaigns.example.yaml
"""
import argparse
import copy
import time

import yaml
from dotenv import load_dotenv

from src.ad import create_ad
from src.adset import create_adset
from src.campaign import create_campaign
from src.creative import (
    create_creative_from_existing_post,
    create_creative_from_image,
    create_creative_from_video,
    upload_image,
    upload_video,
)
from src.fb_client import get_ad_account, init_api
from src import sheet_client
from src.message_templates import get_template_json

# Cấu hình mặc định (giữ nguyên như mẫu trước đây trong config/campaigns.yaml)
# dùng chung cho MỌI campaign được tạo từ Google Sheet. Chỉ tên campaign, ngân
# sách, page_id và post_id là lấy riêng theo từng dòng trong sheet.
SHEET_CAMPAIGN_TEMPLATE: dict = {
    "objective": "OUTCOME_SALES",
    "special_ad_categories": [],
    "status": "ACTIVE",
    "adsets": [
        {
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "MESSAGING_PURCHASE_CONVERSION",
            "destination_type": "MESSENGER",
            "status": "ACTIVE",
            "targeting": {
                "geo_locations": {"countries": ["VN"]},
                "publisher_platforms": ["facebook", "messenger"],
                "device_platforms": ["mobile"],
                "wifi_only": False,
            },
            "ads": [
                {
                    "status": "ACTIVE",
                },
            ],
        },
    ],
}


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_cta_value(ad_cfg: dict) -> dict | None:
    """
    Nếu ad khai báo destination: "messenger" trong config, trả về cta_value
    trỏ nút CTA đến Messenger thay vì link web. Ngược lại trả None (dùng
    hành vi mặc định là {"link": link}).
    """
    if ad_cfg.get("destination") == "messenger":
        return {"app_destination": "MESSENGER"}
    return None


def _build_creative(account, ad_cfg: dict):
    """Chọn cách tạo creative dựa trên các field có trong config của ad."""
    name = f"Creative - {ad_cfg['name']}"

    if ad_cfg.get("existing_post_id"):
        return create_creative_from_existing_post(
            account,
            page_id=ad_cfg["page_id"],
            post_id=ad_cfg["existing_post_id"],
            name=name,
            call_to_action_type=ad_cfg.get("call_to_action"),
            page_welcome_message=ad_cfg.get("page_welcome_message"),
        )

    if ad_cfg.get("existing_video_id"):
        # Dùng trực tiếp 1 video/Reel đã có sẵn trên Page (không cần upload lại)
        return create_creative_from_video(
            account,
            page_id=ad_cfg["page_id"],
            message=ad_cfg.get("message", ""),
            video_id=ad_cfg["existing_video_id"],
            thumbnail_url=ad_cfg["thumbnail_url"],
            name=name,
            call_to_action_type=ad_cfg.get("call_to_action", "SHOP_NOW"),
            link=ad_cfg.get("link", ""),
            cta_value=_build_cta_value(ad_cfg),
        )

    if ad_cfg.get("video_path"):
        video_id = upload_video(account, ad_cfg["video_path"])
        return create_creative_from_video(
            account,
            page_id=ad_cfg["page_id"],
            message=ad_cfg.get("message", ""),
            video_id=video_id,
            thumbnail_url=ad_cfg["thumbnail_url"],
            name=name,
            call_to_action_type=ad_cfg.get("call_to_action", "SHOP_NOW"),
            link=ad_cfg.get("link", ""),
            cta_value=_build_cta_value(ad_cfg),
        )

    if ad_cfg.get("image_path"):
        image_hash = upload_image(account, ad_cfg["image_path"])
        return create_creative_from_image(
            account,
            page_id=ad_cfg["page_id"],
            message=ad_cfg.get("message", ""),
            link=ad_cfg.get("link", ""),
            image_hash=image_hash,
            name=name,
            call_to_action_type=ad_cfg.get("call_to_action", "SHOP_NOW"),
        )

    raise ValueError(
        f"Ad '{ad_cfg['name']}' cần có 1 trong 4: existing_post_id / "
        f"existing_video_id / image_path / video_path trong file config."
    )


def process_campaign_config(account, camp_cfg: dict) -> dict:
    """
    Tạo 1 cây Campaign -> AdSet(s) -> Ad(s) dựa theo camp_cfg (dict cùng cấu
    trúc với 1 phần tử trong config/campaigns.yaml). Dùng chung cho cả 2 nguồn
    dữ liệu: đọc từ file YAML (run) hoặc đọc từ Google Sheet (run_from_sheet).

    Trả về dict {"campaign_id", "adset_ids": [...], "ad_ids": [...]} để nơi gọi
    (VD: run_from_sheet) có thể ghi lại ID vừa tạo. Ném exception ngay khi có
    lỗi - nơi gọi tự quyết định xử lý (in ra / ghi lỗi vào Google Sheet...).
    """
    print(f"\n=== Campaign: {camp_cfg['name']} ===")
    adset_ids: list[str] = []
    ad_ids: list[str] = []

    campaign = create_campaign(
        account,
        name=camp_cfg["name"],
        objective=camp_cfg.get("objective", "OUTCOME_ENGAGEMENT"),
        status=camp_cfg.get("status", "PAUSED"),
        special_ad_categories=camp_cfg.get("special_ad_categories", []),
        daily_budget=camp_cfg.get("daily_budget"),
        # bid_strategy phải khai báo ở Campaign khi dùng CBO
        bid_strategy=(
            camp_cfg.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP")
            if camp_cfg.get("daily_budget")
            else None
        ),
        bid_amount=camp_cfg.get("bid_amount"),
    )
    campaign_id = campaign["id"]
    print(f"  -> Campaign ID: {campaign_id}")

    for adset_cfg in camp_cfg.get("adsets", []):
        print(f"  --- AdSet: {adset_cfg['name']}")
        uses_campaign_budget = bool(camp_cfg.get("daily_budget"))
        adset = create_adset(
            account,
            name=adset_cfg["name"],
            campaign_id=campaign_id,
            # Chỉ truyền daily_budget/bid_strategy ở AdSet khi KHÔNG dùng
            # ngân sách cấp Campaign (CBO) - nếu dùng CBO thì 2 field này
            # đã khai báo ở create_campaign() rồi, để trống ở đây.
            daily_budget=(
                None if uses_campaign_budget else adset_cfg.get("daily_budget")
            ),
            billing_event=adset_cfg.get("billing_event", "IMPRESSIONS"),
            optimization_goal=adset_cfg.get(
                "optimization_goal", "POST_ENGAGEMENT"
            ),
            targeting=adset_cfg["targeting"],
            status=adset_cfg.get("status", "PAUSED"),
            bid_strategy=(
                None
                if uses_campaign_budget
                else adset_cfg.get("bid_strategy", "LOWEST_COST_WITHOUT_CAP")
            ),
            bid_amount=adset_cfg.get("bid_amount"),
            start_time=adset_cfg.get("start_time"),
            # Chỉ dùng promoted_object khi tự khai báo rõ trong config
            # (VD chạy chiến dịch Lượt thích Trang) - KHÔNG tự suy ra,
            # vì dễ khiến Facebook hiểu nhầm sang loại chiến dịch khác.
            promoted_object=adset_cfg.get("promoted_object"),
            destination_type=adset_cfg.get("destination_type"),
        )
        adset_id = adset["id"]
        adset_ids.append(adset_id)
        print(f"      -> AdSet ID: {adset_id}")

        for ad_cfg in adset_cfg.get("ads", []):
            print(f"      ..... Ad: {ad_cfg['name']}")
            creative = _build_creative(account, ad_cfg)
            ad = create_ad(
                account,
                name=ad_cfg["name"],
                adset_id=adset_id,
                creative_id=creative["id"],
                status=ad_cfg.get("status", "PAUSED"),
            )
            ad_ids.append(ad["id"])
            print(f"          -> Ad ID: {ad['id']}")

    return {"campaign_id": campaign_id, "adset_ids": adset_ids, "ad_ids": ad_ids}


def run(config_path: str) -> None:
    """Chạy từ 1 file cấu hình YAML cố định (cách cũ)."""
    load_dotenv()

    config = load_config(config_path)

    init_api()

    account = get_ad_account()

    for camp_cfg in config.get("campaigns", []):
        process_campaign_config(account, camp_cfg)

    print("\nHoàn tất!")


def _numbered_name(base: str, label: str, index: int, total: int) -> str:
    return base if total == 1 else f"{base} - {label}{index}"


def _ads_per_adset(adset_count: int, ad_count: int) -> list[int]:
    """Phân bổ tổng số quảng cáo lần lượt và không tạo nhóm rỗng."""
    base, remainder = divmod(ad_count, adset_count)
    return [base + (1 if index < remainder else 0) for index in range(adset_count)]


def _build_campaign_configs_from_row(row: "sheet_client.SheetRow") -> list[dict]:
    """
    Ghép 1 dòng dữ liệu từ Google Sheet vào cấu hình mẫu SHEET_CAMPAIGN_TEMPLATE
    để ra 1 camp_cfg đầy đủ, dùng được cho process_campaign_config().
    Số Campaign/AdSet/tổng Ad lấy từ Camp_Structure của từng dòng.
    """
    configs: list[dict] = []
    distribution = _ads_per_adset(row.adset_count, row.ad_count)
    base_name = row.group_ad_name or row.campaign_name
    for campaign_index in range(1, row.campaign_count + 1):
        cfg = copy.deepcopy(SHEET_CAMPAIGN_TEMPLATE)
        cfg["name"] = _numbered_name(
            row.campaign_name, "C", campaign_index, row.campaign_count
        )
        cfg["daily_budget"] = row.daily_budget
        template_adset = cfg["adsets"][0]
        cfg["adsets"] = []
        ads_before = 0
        for adset_index, ads_in_adset in enumerate(distribution, start=1):
            adset_cfg = copy.deepcopy(template_adset)
            adset_cfg["name"] = _numbered_name(
                base_name, "AS", adset_index, row.adset_count
            )
            adset_cfg["promoted_object"] = {"page_id": row.page_id}
            targeting = adset_cfg["targeting"]
            if row.age_min is not None and row.age_max is not None:
                targeting["age_min"] = row.age_min
                targeting["age_max"] = row.age_max
            if row.genders is not None:
                targeting["genders"] = row.genders
            if row.schedule:
                adset_cfg["start_time"] = row.schedule

            template_ad = adset_cfg["ads"][0]
            adset_cfg["ads"] = []
            for ad_index in range(1, ads_in_adset + 1):
                ad_cfg = copy.deepcopy(template_ad)
                global_ad_index = ads_before + ad_index
                ad_cfg["name"] = _numbered_name(
                    base_name, "AD", global_ad_index, row.ad_count
                )
                ad_cfg["page_id"] = row.page_id
                ad_cfg["existing_post_id"] = row.post_id
                if row.message_template_name:
                    ad_cfg["page_welcome_message"] = get_template_json(
                        row.message_template_name
                    )
                adset_cfg["ads"].append(ad_cfg)
            ads_before += ads_in_adset
            cfg["adsets"].append(adset_cfg)
        configs.append(cfg)
    return configs


def run_from_sheet() -> None:
    """
    Chạy từ Google Sheet: mỗi dòng dữ liệu (chưa có kết quả ở cột Kết quả) sẽ
    tạo cấu trúc Campaign/AdSet/Ad theo cột Camp_Structure, dùng chung cấu hình mẫu
    SHEET_CAMPAIGN_TEMPLATE (targeting, objective...). Sau khi tạo xong (hoặc
    lỗi), ghi kết quả ngược lại cột `RESULT` của đúng dòng đó.
    """
    load_dotenv()

    init_api()

    sheet_started = time.perf_counter()
    worksheet = sheet_client.get_worksheet()
    rows = sheet_client.read_rows(worksheet)
    print(
        f"Đọc Google Sheet: {time.perf_counter() - sheet_started:.2f}s",
        flush=True,
    )

    if not rows:
        print("Không có dòng nào cần tạo (sheet trống hoặc tất cả đã có kết quả).")
        return

    print(f"Tìm thấy {len(rows)} dòng cần tạo campaign.", flush=True)
    accounts: dict[str, object] = {}

    for row in rows:
        row_started = time.perf_counter()
        try:
            account = accounts.get(row.ad_account_id)
            if account is None:
                account = get_ad_account(row.ad_account_id)
                accounts[row.ad_account_id] = account
            results = [
                process_campaign_config(account, camp_cfg)
                for camp_cfg in _build_campaign_configs_from_row(row)
            ]
            campaign_ids = [result["campaign_id"] for result in results]
            adset_count = sum(len(result["adset_ids"]) for result in results)
            ad_count = sum(len(result["ad_ids"]) for result in results)
            message = (
                f"Thành công {row.campaign_count}-{row.adset_count}-{row.ad_count} - "
                f"Campaign: {', '.join(campaign_ids)}, "
                f"AdSet: {adset_count}, Ad: {ad_count}"
            )
            sheet_client.write_result(worksheet, row.row_number, message)
            print(
                f"  -> Dòng {row.row_number}: {message} "
                f"({time.perf_counter() - row_started:.2f}s)",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001 - cố tình bắt mọi lỗi để ghi vào sheet
            error_message = f"Lỗi: {e}"
            print(
                f"  -> Dòng {row.row_number}: {error_message} "
                f"({time.perf_counter() - row_started:.2f}s)",
                flush=True,
            )
            sheet_client.write_result(worksheet, row.row_number, error_message)

    print("\nHoàn tất!")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tự động tạo Campaign/AdSet/Ad trên Facebook Ads Manager"
    )
    parser.add_argument(
        "config",
        nargs="?",
        help="Đường dẫn tới file config YAML (bỏ qua nếu dùng --from-sheet)",
    )
    parser.add_argument(
        "--from-sheet",
        action="store_true",
        help="Đọc danh sách campaign cần tạo từ Google Sheet thay vì file YAML",
    )
    args = parser.parse_args()

    if args.from_sheet:
        run_from_sheet()
    else:
        if not args.config:
            parser.error("cần truyền đường dẫn config YAML, hoặc dùng --from-sheet")
        run(args.config)


if __name__ == "__main__":
    main()
