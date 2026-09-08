"""Workflow 12: workflow 03 campaign settings with a new video creative."""
from __future__ import annotations

from dataclasses import dataclass
import time

from dotenv import load_dotenv

from src import sheet_client
from src.cli import _build_campaign_configs_from_row, process_campaign_config
from src.creative import wait_for_video_thumbnail
from src.fb_client import get_ad_account, init_api
from src.website_results import WebsiteResultWriter, read_post_once


@dataclass(frozen=True)
class VideoAsset:
    row_number: int
    video_id: str
    title: str
    text_content: str
    image_hash: str = ""


def read_video_assets(worksheet) -> dict[str, VideoAsset]:
    values = worksheet.get_all_values()
    headers = sheet_client._build_header_map(values[0] if values else [])
    names = ["Mã", "FB_UPLOAD_ID", "Title", "Text_Content"]
    columns = {name: sheet_client._col_to_index(headers, name) for name in names}
    image_hash_column = headers.get(sheet_client._normalize_header("Image Hash"))
    assets = {}
    for number, row in enumerate(values[1:], start=2):
        data = {name: row[index].strip() if index < len(row) else "" for name, index in columns.items()}
        code = data["Mã"]
        if not code:
            continue
        if code in assets:
            raise ValueError(f"Mã '{code}' bị trùng trong Bài viết (hàng {number})")
        assets[code] = VideoAsset(
            row_number=number,
            video_id=data["FB_UPLOAD_ID"],
            title=data["Title"],
            text_content=data["Text_Content"],
            image_hash=row[image_hash_column].strip() if image_hash_column is not None and image_hash_column < len(row) else "",
        )
    return assets


def require_asset(assets: dict[str, VideoAsset], code: str | None) -> VideoAsset:
    if not code:
        raise ValueError("Thiếu Mã trong Lên Camp để tìm video trong Bài viết")
    if code not in assets:
        raise ValueError(f"Không tìm thấy Mã '{code}' trong Bài viết")
    asset = assets[code]
    missing = [name for name, value in [
        ("FB_UPLOAD_ID", asset.video_id), ("Title", asset.title), ("Text_Content", asset.text_content),
    ] if not value]
    if missing:
        raise ValueError(f"Mã '{code}' thiếu {', '.join(missing)} trong Bài viết")
    if not asset.video_id.isascii() or not asset.video_id.isdigit():
        raise ValueError(f"Mã '{code}': FB_UPLOAD_ID phải là một ID video dạng số, không phải link/danh sách")
    return asset


def build_video_configs(
    row, asset: VideoAsset, thumbnail_url: str, image_hash: str = ""
) -> list[dict]:
    configs = _build_campaign_configs_from_row(row)
    for config in configs:
        for adset in config["adsets"]:
            for ad in adset["ads"]:
                ad.pop("existing_post_id", None)
                ad.update(
                    existing_video_id=asset.video_id,
                    title=asset.title,
                    message=asset.text_content,
                    thumbnail_url=thumbnail_url,
                    image_hash=image_hash,
                    call_to_action="MESSAGE_PAGE",
                    destination="messenger",
                )
    return configs


def image_hash_for_account(value: str, ad_account_id: str) -> str:
    """Return a hash only when its optional account prefix matches this ad account."""
    value = (value or "").strip()
    if not value:
        return ""
    account_id, separator, image_hash = value.partition(":")
    if not separator:
        return value
    if account_id.removeprefix("act_") != ad_account_id.removeprefix("act_"):
        return ""
    return image_hash.strip()


def main() -> None:
    load_dotenv()
    init_api()
    worksheet = sheet_client.get_worksheet()
    rows = sheet_client.read_rows(worksheet, require_post_id=False)
    if not rows:
        print("Không có dòng nào cần tạo campaign.", flush=True)
        return
    article_worksheet = sheet_client.get_worksheet("Bài viết")
    assets = read_video_assets(article_worksheet)
    article_results = WebsiteResultWriter(article_worksheet)
    accounts = {}
    thumbnails = {}
    failures = 0
    print(f"Workflow 12: {len(rows)} dòng cần xử lý.", flush=True)
    for row in rows:
        started = time.monotonic()
        try:
            asset = require_asset(assets, row.group_ad_name)
            # Validate CHAT_TEMPLATE and the complete structure before creating campaigns.
            image_hash = image_hash_for_account(asset.image_hash, row.ad_account_id)
            configs = build_video_configs(row, asset, "", image_hash)
            if image_hash:
                print(f"Dòng {row.row_number}: dùng Image Hash, không gọi thumbnail FB_UPLOAD_ID", flush=True)
            else:
                key = (row.ad_account_id, asset.video_id)
                if key not in thumbnails:
                    print(f"Dòng {row.row_number}: lấy thumbnail video {asset.video_id} (tối đa 600 giây)", flush=True)
                    thumbnails[key] = wait_for_video_thumbnail(asset.video_id)
                for config in configs:
                    for adset in config["adsets"]:
                        for ad in adset["ads"]:
                            ad["thumbnail_url"] = thumbnails[key]
            if row.ad_account_id not in accounts:
                accounts[row.ad_account_id] = get_ad_account(row.ad_account_id)
            results = [process_campaign_config(accounts[row.ad_account_id], config) for config in configs]
            post_results = [
                read_post_once(creative_id)
                for result in results
                for creative_id in result["creative_ids"]
            ]
            article_results.write_posts(asset.row_number, post_results)
            campaign_ids = [result["campaign_id"] for result in results]
            message = (
                f"Thành công Video {row.campaign_count}-{row.adset_count}-{row.ad_count} - "
                f"Campaign: {', '.join(campaign_ids)}, "
                f"AdSet: {sum(len(result['adset_ids']) for result in results)}, "
                f"Ad: {sum(len(result['ad_ids']) for result in results)}"
            )
        except Exception as exc:
            failures += 1
            message = f"Lỗi Video: {exc}"
        sheet_client.write_result(worksheet, row.row_number, message)
        print(f"Dòng {row.row_number}: {message} ({time.monotonic() - started:.1f}s)", flush=True)
    if failures:
        raise RuntimeError(f"Có {failures} dòng tạo campaign video thất bại")


if __name__ == "__main__":
    main()
