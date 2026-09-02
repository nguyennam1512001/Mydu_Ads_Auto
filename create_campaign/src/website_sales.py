from __future__ import annotations

import argparse
import asyncio
import time

from dotenv import load_dotenv

from src.ad import create_ad
from src.adset import create_adset
from src.campaign import create_campaign
from src.creative import create_creative_from_existing_post
from src.fb_client import get_ad_account, init_api
from src.sheet_client import (
    get_worksheet,
    read_website_sales_rows,
    write_result,
)


async def run(*, limit: int | None = None) -> None:
    load_dotenv()
    init_api()
    worksheet = get_worksheet()
    rows = read_website_sales_rows(worksheet)
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        print("Không có dòng nào cần tạo quảng cáo Website.")
        return

    failures = 0
    accounts: dict[str, object] = {}
    for row in rows:
        started = time.monotonic()
        try:
            account = accounts.get(row.ad_account_id)
            if account is None:
                account = get_ad_account(row.ad_account_id)
                accounts[row.ad_account_id] = account

            ad_name = row.group_ad_name or row.campaign_name
            # Existing-post creatives must reuse the public post unchanged.
            # Adding/overriding a CTA here can make Meta treat it as an
            # unpublished development ad post (error_subcode 1885183).
            creative = create_creative_from_existing_post(
                account,
                page_id=row.page_id,
                post_id=row.post_id,
                name=f"Creative - {ad_name}",
            )

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
