import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src import cli
from src.facebook_client import FacebookPagePublisher
from src.sheet_client import REQUIRED_COLUMNS, SheetRepository


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def pending(self, **values):
        repo = SheetRepository.__new__(SheetRepository)
        repo.worksheet = Mock()
        repo.worksheet.get_all_values.return_value = [
            REQUIRED_COLUMNS,
            [values.get(name, "") for name in REQUIRED_COLUMNS],
        ]
        repo.header_map = {}
        return repo, repo.pending_rows()

    def test_uploaded_video_needs_no_content_or_telegram(self):
        for post_id, link in [("", ""), ("123", ""), ("", "saved-link")]:
            with self.subTest(post_id=post_id, link=link):
                _, rows = self.pending(
                    PAGE_ID="page", FB_UPLOAD_ID="video",
                    POST_ID=post_id, **{"Post Link": link},
                )
                self.assertEqual(len(rows), 1)

    def test_complete_row_is_skipped(self):
        _, rows = self.pending(
            PAGE_ID="page", FB_UPLOAD_ID="video", POST_ID="123",
            **{"Post Link": "link"},
        )
        self.assertEqual(rows, [])

    def test_all_incomplete_combinations_are_pending(self):
        for mask in range(7):
            with self.subTest(mask=mask):
                _, rows = self.pending(
                    PAGE_ID="page", Text_Content="text", Telegram_video_link="telegram",
                    FB_UPLOAD_ID="video" if mask & 1 else "",
                    POST_ID="post" if mask & 2 else "",
                    **{"Post Link": "link" if mask & 4 else ""},
                )
                self.assertEqual(len(rows), 1)

    async def test_missing_video_uses_existing_post_never_uploads(self):
        for post_id, link, resolved_video in [
            ("post", "", "video"), ("", "link", "video"),
            ("post", "link", "video"), ("post", "", ""),
        ]:
            with self.subTest(post_id=post_id, link=link, video=resolved_video):
                _, rows = self.pending(PAGE_ID="page", POST_ID=post_id, **{"Post Link": link})
                sheet = Mock()
                sheet.pending_rows.return_value = rows
                publisher = Mock()
                publisher.recover_video_reference.return_value = SimpleNamespace(
                    video_id=resolved_video, post_id="post", permalink_url="link",
                )
                with patch.object(cli, "load_dotenv"), patch.object(cli, "Settings") as settings, patch.object(cli, "SheetRepository", return_value=sheet), patch.object(cli, "FacebookPagePublisher", return_value=publisher), patch.object(cli, "TelegramDownloader") as telegram:
                    settings.from_env.return_value = Mock()
                    await cli.run()
                telegram.assert_not_called()
                publisher.upload_video.assert_not_called()
                publisher.wait_for_post.assert_not_called()
                publisher.recover_video_reference.assert_called_once_with("page", post_id, link)
                self.assertEqual(
                    sheet.update.call_args.kwargs["POST_STATUS"].startswith("Lỗi:"),
                    not bool(resolved_video),
                )

    def test_video_link_resolution_needs_no_api(self):
        publisher = FacebookPagePublisher("test")
        publisher.http = Mock()
        for link in ["https://www.facebook.com/reel/123", "https://www.facebook.com/page/videos/123/", "https://www.facebook.com/watch/?v=123"]:
            self.assertEqual(publisher.recover_video_reference("page", "", link).video_id, "123")
        publisher.http.get.assert_not_called()

    def test_post_reference_uses_video_object_not_post_id(self):
        publisher = FacebookPagePublisher("test")
        publisher._page_token = Mock(return_value="token")
        publisher.http = Mock()
        publisher.http.get.return_value.ok = True
        publisher.http.get.return_value.json.return_value = {
            "id": "page_456", "object_id": "123", "type": "video",
            "permalink_url": "https://www.facebook.com/page/posts/456",
        }
        for post_id, link in [("456", ""), ("page_456", ""), ("", "https://www.facebook.com/page/posts/456")]:
            post = publisher.recover_video_reference("page", post_id, link)
            self.assertEqual(post.video_id, "123")
            self.assertTrue(publisher.http.get.call_args.args[0].endswith("/page_456"))
        publisher.http.get.return_value.json.return_value["type"] = "photo"
        self.assertEqual(publisher.recover_video_reference("page", "456", "").video_id, "")

    def test_missing_page_is_reported(self):
        repo, rows = self.pending(FB_UPLOAD_ID="video")
        self.assertEqual(rows, [])
        repo.worksheet.batch_update.assert_called_once()

    def test_new_post_validation_is_preserved(self):
        _, rows = self.pending(PAGE_ID="page", Text_Content="text")
        self.assertEqual(rows, [])
        _, rows = self.pending(
            PAGE_ID="page", Text_Content="text", Telegram_video_link="link",
        )
        self.assertEqual(len(rows), 1)

    async def test_recovery_never_uploads_and_preserves_existing_values(self):
        for post_id, link, fails in [
            ("", "", False), ("saved-id", "", False),
            ("", "saved-link", False), ("", "", True),
        ]:
            with self.subTest(post_id=post_id, link=link, fails=fails):
                _, rows = self.pending(
                    PAGE_ID="page", FB_UPLOAD_ID="video",
                    POST_ID=post_id, **{"Post Link": link},
                )
                sheet = Mock()
                sheet.pending_rows.return_value = rows
                publisher = Mock()
                publisher.wait_for_post.return_value = SimpleNamespace(
                    video_id="video", post_id="resolved-id", permalink_url="resolved-link",
                )
                if fails:
                    publisher.wait_for_post.side_effect = TimeoutError("pending")
                with patch.object(cli, "load_dotenv"), patch.object(cli, "Settings") as settings, patch.object(cli, "SheetRepository", return_value=sheet), patch.object(cli, "FacebookPagePublisher", return_value=publisher), patch.object(cli, "TelegramDownloader") as telegram:
                    settings.from_env.return_value = Mock()
                    await cli.run()
                telegram.assert_not_called()
                publisher.upload_video.assert_not_called()
                publisher.recover_post_id.assert_not_called()
                publisher.wait_for_post.assert_called_once_with("page", "video")
                result = sheet.update.call_args.kwargs
                if fails:
                    self.assertTrue(result["POST_STATUS"].startswith("Lỗi:"))
                    self.assertNotIn("POST_ID", result)
                else:
                    self.assertEqual(result["POST_ID"], post_id or "resolved-id")
                    self.assertEqual(result["Post Link"], link or "resolved-link")


if __name__ == "__main__":
    unittest.main()
