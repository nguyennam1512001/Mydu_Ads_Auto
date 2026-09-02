from __future__ import annotations

import argparse
import asyncio
import tempfile
import time
from contextlib import AsyncExitStack
from pathlib import Path

from dotenv import load_dotenv

from src.facebook_client import FacebookPagePublisher
from src.settings import Settings
from src.sheet_client import (
    COL_POST_ID,
    COL_POST_LINK,
    COL_STATUS,
    COL_VIDEO_ID,
    SheetRepository,
)
from src.telegram_client import TelegramDownloader, parse_message_link


async def run(*, dry_run: bool = False, limit: int | None = None) -> None:
    load_dotenv()
    settings = Settings.from_env(dry_run=dry_run)
    sheet = SheetRepository(
        settings.google_sheet_id,
        settings.google_sheet_tab,
        settings.google_credentials,
    )
    sheet_started = time.monotonic()
    rows = sheet.pending_rows()
    print(f"Đọc Google Sheet: {time.monotonic() - sheet_started:.1f}s")
    if limit is not None:
        rows = rows[:limit]

    if not rows:
        print("Không có dòng nào cần đăng bài.")
        return

    print(f"Tìm thấy {len(rows)} dòng cần xử lý.")
    if dry_run:
        for row in rows:
            if row.post_link:
                print(
                    f"[DRY-RUN] Dòng {row.row_number}: lấy POST_ID từ "
                    f"{row.post_link}"
                )
                continue
            ref = parse_message_link(row.telegram_link)
            print(
                f"[DRY-RUN] Dòng {row.row_number}: Page {row.page_id}, "
                f"Telegram entity={ref.entity}, message={ref.message_id}"
            )
        return

    publisher = FacebookPagePublisher(
        settings.fb_access_token,
        settings.fb_graph_version,
    )
    async with AsyncExitStack() as stack:
        telegram: TelegramDownloader | None = None
        for row in rows:
            row_started = time.monotonic()
            try:
                sheet.update(row.row_number, **{COL_STATUS: "Đang xử lý"})
                if row.post_link and not row.post_id:
                    recover_started = time.monotonic()
                    post_id = publisher.recover_post_id(
                        row.page_id,
                        row.post_link,
                    )
                    print(
                        f"Dòng {row.row_number}: lấy POST_ID mất "
                        f"{time.monotonic() - recover_started:.1f}s"
                    )
                    sheet.update(
                        row.row_number,
                        **{
                            COL_POST_ID: post_id,
                            COL_STATUS: "Thành công",
                        },
                    )
                    print(
                        f"Dòng {row.row_number}: đã lấy POST_ID {post_id} "
                        "từ Post Link"
                    )
                    continue

                video_id = row.video_id
                if not video_id:
                    if telegram is None:
                        telegram = await stack.enter_async_context(
                            TelegramDownloader(
                                settings.telegram_api_id,
                                settings.telegram_api_hash,
                                settings.telegram_session,
                            )
                        )
                    with tempfile.TemporaryDirectory(prefix="auto-post-page-") as temp_dir:
                        download_started = time.monotonic()
                        video_path = await telegram.download_video(
                            row.telegram_link,
                            Path(temp_dir),
                        )
                        print(
                            f"Dòng {row.row_number}: tải Telegram mất "
                            f"{time.monotonic() - download_started:.1f}s"
                        )
                        upload_started = time.monotonic()
                        video_id = publisher.upload_video(
                            row.page_id,
                            video_path,
                            row.text_content,
                            title=row.description,
                        )
                        print(
                            f"Dòng {row.row_number}: upload Meta mất "
                            f"{time.monotonic() - upload_started:.1f}s"
                        )
                    sheet.update(
                        row.row_number,
                        **{
                            COL_VIDEO_ID: video_id,
                            COL_STATUS: "Đã upload, đang chờ Meta xử lý",
                        },
                    )

                wait_started = time.monotonic()
                post = publisher.wait_for_post(row.page_id, video_id)
                print(
                    f"Dòng {row.row_number}: chờ Meta tạo bài mất "
                    f"{time.monotonic() - wait_started:.1f}s"
                )
                sheet.update(
                    row.row_number,
                    **{
                        COL_VIDEO_ID: post.video_id,
                        COL_POST_ID: post.post_id,
                        COL_POST_LINK: post.permalink_url,
                        COL_STATUS: "Thành công",
                    },
                )
                print(
                    f"Dòng {row.row_number}: {post.permalink_url} "
                    f"(tổng {time.monotonic() - row_started:.1f}s)"
                )
            except Exception as exc:  # noqa: BLE001
                message = f"Lỗi: {exc}"
                sheet.update(row.row_number, **{COL_STATUS: message[:500]})
                print(f"Dòng {row.row_number}: {message}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tự động đăng video lên Facebook Page")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra dữ liệu")
    parser.add_argument("--limit", type=int, help="Giới hạn số dòng xử lý")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run, limit=args.limit))
