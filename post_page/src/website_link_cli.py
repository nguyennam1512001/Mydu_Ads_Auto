from __future__ import annotations

import argparse
import os
import time

from dotenv import load_dotenv

from src.facebook_client import FacebookPagePublisher
from src.sheet_client import (
    COL_POST_ID,
    COL_POST_LINK,
    COL_STATUS,
    SheetRepository,
)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(f"Thiếu biến môi trường: {name}")
    return value


def run(*, limit: int | None = None) -> None:
    load_dotenv()
    sheet = SheetRepository(
        required_env("GOOGLE_SHEET_ID"),
        os.getenv("GOOGLE_SHEET_TAB", "Bài viết"),
        required_env("GOOGLE_CREDENTIALS"),
    )
    rows = sheet.pending_website_link_rows()
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        print("Không có dòng nào cần đăng bài link Website.")
        return

    publisher = FacebookPagePublisher(
        required_env("FB_ACCESS_TOKEN"),
        os.getenv("FB_GRAPH_VERSION", "v25.0"),
    )
    failures = 0
    for row in rows:
        started = time.monotonic()
        try:
            sheet.update(row.row_number, **{COL_STATUS: "Đang đăng bài link Website"})
            post = publisher.publish_website_link_post(
                row.page_id,
                row.text_content,
                row.website_url,
            )
            sheet.update(
                row.row_number,
                **{
                    COL_POST_ID: post.post_id,
                    COL_POST_LINK: post.permalink_url,
                    COL_STATUS: "Thành công - bài link Website",
                },
            )
            print(
                f"Dòng {row.row_number}: {post.permalink_url} "
                f"({time.monotonic() - started:.1f}s)"
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            message = f"Lỗi bài link Website: {exc}"
            sheet.update(row.row_number, **{COL_STATUS: message[:500]})
            print(f"Dòng {row.row_number}: {message}")

    if failures:
        raise RuntimeError(f"Có {failures} dòng đăng bài link Website thất bại")


def main() -> None:
    parser = argparse.ArgumentParser(description="Đăng bài link Website lên Facebook Page")
    parser.add_argument("--limit", type=int, help="Giới hạn số dòng xử lý")
    args = parser.parse_args()
    run(limit=args.limit)


if __name__ == "__main__":
    main()
