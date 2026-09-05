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
from src.website_results import WebsiteResultWriter, read_post_once


def numbered_name(base: str, label: str, index: int, total: int) -> str:
    return base if total == 1 else f"{base} - {label}{index}"


def ads_per_adset(adset_count: int, ad_count: int) -> list[int]:
    base, remainder = divmod(ad_count, adset_count)
    return [base + (1 if index < remainder else 0) for index in range(adset_count)]


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
    asset_worksheet = get_worksheet("Bài viết")
    assets = read_website_assets(asset_worksheet)
    rows = read_website_sales_rows(worksheet, assets)
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        print("Không có dòng nào cần tạo quảng cáo Website.")
        return

    result_writer = WebsiteResultWriter(asset_worksheet)
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
                    result_writer.write_upload(row.group_ad_name, video_id)
                    thumbnail_url = wait_for_video_thumbnail(video_id)

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

                base_name = row.group_ad_name or row.campaign_name
                distribution = ads_per_adset(row.adset_count, row.ad_count)
                campaign_ids: list[str] = []
                adset_ids: list[str] = []
                ad_ids: list[str] = []
                post_results: list[tuple[str, str]] = []
                for campaign_index in range(1, row.campaign_count + 1):
                    campaign = create_campaign(
                        account,
                        name=numbered_name(
                            row.campaign_name,
                            "C",
                            campaign_index,
                            row.campaign_count,
                        ),
                        objective="OUTCOME_SALES",
                        status="PAUSED",
                        daily_budget=row.daily_budget,
                        bid_strategy="LOWEST_COST_WITHOUT_CAP",
                        special_ad_categories=[],
                    )
                    campaign_id = campaign["id"]
                    campaign_ids.append(campaign_id)
                    ads_before = 0
                    for adset_index, ads_in_adset in enumerate(distribution, start=1):
                        adset = create_adset(
                            account,
                            name=numbered_name(
                                f"AdSet - {base_name}",
                                "AS",
                                adset_index,
                                row.adset_count,
                            ),
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
                        adset_ids.append(adset["id"])
                        for ad_index in range(1, ads_in_adset + 1):
                            global_ad_index = ads_before + ad_index
                            ad_name = numbered_name(
                                f"Ad - {base_name}",
                                "AD",
                                global_ad_index,
                                row.ad_count,
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
                                name=ad_name,
                                adset_id=adset["id"],
                                creative_id=creative["id"],
                                status="ACTIVE",
                            )
                            ad_ids.append(ad["id"])
                            post_results.append(read_post_once(str(creative["id"])))
                            result_writer.write_posts(row.group_ad_name, post_results)
                        ads_before += ads_in_adset
                message = (
                    f"Thành công Website {row.campaign_count}-"
                    f"{row.adset_count}-{row.ad_count} - Campaign: "
                    f"{', '.join(campaign_ids)}, AdSet: {len(adset_ids)}, "
                    f"Ad: {len(ad_ids)}"
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
