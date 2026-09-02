from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

from src.ad import create_ad
from src.adset import create_adset
from src.campaign import create_campaign
from src.creative import (
    create_creative_from_video,
    upload_video,
    wait_for_video_thumbnail,
)
from src.fb_client import get_ad_account, init_api
from src.sheet_client import (
    get_worksheet,
    read_website_assets,
    read_website_sales_rows,
    write_result,
)
from src.telegram_client import TelegramDownloader


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(f"Thiếu biến môi trường: {name}")
    return value


async def run(*, limit: int | None = None) -> None:
    load_dotenv()
    init_api()
    try:
        telegram_api_id = int(required_env("TELEGRAM_API_ID"))
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID phải là số nguyên") from exc

    worksheet = get_worksheet()
    assets = read_website_assets(get_worksheet("Bài viết"))
    rows = read_website_sales_rows(worksheet, assets)
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        print("Không có dòng nào cần tạo quảng cáo Website.")
        return

    failures = 0
    accounts: dict[str, object] = {}
    async with TelegramDownloader(
        telegram_api_id,
        required_env("TELEGRAM_API_HASH"),
        required_env("TELEGRAM_SESSION"),
    ) as telegram:
        for row in rows:
            started = time.monotonic()
            try:
                account = accounts.get(row.ad_account_id)
                if account is None:
                    account = get_ad_account(row.ad_account_id)
                    accounts[row.ad_account_id] = account

                with tempfile.TemporaryDirectory(prefix="website-sales-") as temp_dir:
                    video_path = await telegram.download_video(
                        row.telegram_link, Path(temp_dir)
                    )
                    video_id = upload_video(account, str(video_path))
                    thumbnail_url = wait_for_video_thumbnail(video_id)

                campaign = create_campaign(
                    account,
                    name=row.campaign_name,
                    objective="OUTCOME_SALES",
                    status="PAUSED",
                    daily_budget=row.daily_budget,
                    bid_strategy="LOWEST_COST_WITHOUT_CAP",
                    special_ad_categories=[],
                )
                campaign_id = campaign["id"]
                targeting = {
                    "geo_locations": {"countries": ["VN"]},
                    "publisher_platforms": ["facebook", "messenger"],
                    "device_platforms": ["mobile"],
                    "wifi_only": False,
                }
                if row.age_min is not None and row.age_max is not None:
                    targeting.update(age_min=row.age_min, age_max=row.age_max)
                if row.genders is not None:
                    targeting["genders"] = row.genders

                ad_name = row.group_ad_name or row.campaign_name
                adset = create_adset(
                    account,
                    name=f"AdSet - {ad_name}",
                    campaign_id=campaign_id,
                    billing_event="IMPRESSIONS",
                    optimization_goal="OFFSITE_CONVERSIONS",
                    targeting=targeting,
                    status="ACTIVE",
                    start_time=row.schedule,
                    promoted_object={
                        "pixel_id": row.pixel_id,
                        "custom_event_type": "PURCHASE",
                    },
                    destination_type="WEBSITE",
                )
                creative = create_creative_from_video(
                    account,
                    page_id=row.page_id,
                    message=row.text_content,
                    title=row.title,
                    video_id=video_id,
                    thumbnail_url=thumbnail_url,
                    name=f"Creative - {ad_name}",
                    call_to_action_type="ORDER_NOW",
                    link=row.website_url,
                )
                ad = create_ad(
                    account,
                    name=f"Ad - {ad_name}",
                    adset_id=adset["id"],
                    creative_id=creative["id"],
                    status="ACTIVE",
                )
                message = (
                    f"Thành công Website - Campaign: {campaign_id}, "
                    f"AdSet: {adset['id']}, Ad: {ad['id']}"
                )
                write_result(worksheet, row.row_number, message)
                print(f"Dòng {row.row_number}: {message}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                message = f"Lỗi Website: {exc}"
                write_result(worksheet, row.row_number, message[:500])
                print(f"Dòng {row.row_number}: {message}")
            finally:
                print(f"Dòng {row.row_number}: {time.monotonic() - started:.1f}s")
    if failures:
        raise RuntimeError(f"Có {failures} dòng tạo quảng cáo Website thất bại")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tạo quảng cáo doanh số Website")
    parser.add_argument("--limit", type=int, help="Giới hạn số dòng xử lý")
    args = parser.parse_args()
    asyncio.run(run(limit=args.limit))


if __name__ == "__main__":
    main()
