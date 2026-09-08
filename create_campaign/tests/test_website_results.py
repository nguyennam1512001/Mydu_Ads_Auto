import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src import website_results, website_sales


class PostReadTests(unittest.TestCase):
    def test_retries_until_meta_returns_post_id_then_builds_link(self):
        with patch.object(website_results, "AdCreative") as creative, patch.object(website_results.time, "sleep") as sleep:
            creative.return_value.api_get.side_effect = [
                {},
                {"effective_object_story_id": "123_456"},
            ]
            self.assertEqual(website_results.read_post_once("creative"), ("456", "https://www.facebook.com/123/posts/456"))
        self.assertEqual(creative.return_value.api_get.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_uses_story_id_without_reading_post(self):
        with patch.object(website_results, "AdCreative") as creative:
            creative.return_value.api_get.return_value = {"effective_object_story_id": "123_456"}
            self.assertEqual(website_results.read_post_once("creative"), ("456", "https://www.facebook.com/123/posts/456"))
            creative.return_value.api_get.assert_called_once_with(fields=["effective_object_story_id"])

    def test_missing_or_failed_creative_never_reads_post_or_retries(self):
        for payload in [{}, RuntimeError("pending")]:
            with self.subTest(payload=payload), patch.object(website_results, "POST_LOOKUP_ATTEMPTS", 1), patch.object(website_results, "AdCreative") as creative:
                if isinstance(payload, Exception):
                    creative.return_value.api_get.side_effect = payload
                else:
                    creative.return_value.api_get.return_value = payload
                self.assertEqual(website_results.read_post_once("creative"), ("", ""))
                creative.return_value.api_get.assert_called_once()


class SheetWriteTests(unittest.TestCase):
    def writer(self):
        sheet = Mock()
        sheet.get_all_values.return_value = [
            ["Post Link", "Mã", "POST_ID", "FB_UPLOAD_ID"],
            ["", "OTHER", "", ""], ["old-link", "CODE", "old-post", "old-video"],
        ]
        return sheet, website_results.WebsiteResultWriter(sheet)

    def test_upload_written_as_text_to_matching_code_and_clears_stale_posts(self):
        sheet, writer = self.writer()
        writer.write_upload("CODE", "1234567890123456789")
        self.assertEqual(sheet.batch_update.call_args.args[0], [
            {"range": "D3", "values": [["1234567890123456789"]]},
            {"range": "C3", "values": [[""]]},
            {"range": "A3", "values": [[""]]},
        ])
        self.assertEqual(sheet.batch_update.call_args.kwargs, {"value_input_option": "RAW"})

    def test_multiple_ads_preserve_missing_positions(self):
        sheet, writer = self.writer()
        writer.write_posts("CODE", [("", ""), ("456", "link")])
        self.assertEqual(sheet.batch_update.call_args.args[0], [
            {"range": "C3", "values": [['[null, "456"]']]},
            {"range": "A3", "values": [['[null, "link"]']]},
        ])
        writer.write_posts("CODE", [("", ""), ("", "")])
        self.assertTrue(all(update["values"] == [[""]] for update in sheet.batch_update.call_args.args[0]))

    def test_missing_headers_fail_before_upload(self):
        sheet = Mock()
        sheet.get_all_values.return_value = [["Mã"]]
        with self.assertRaises(ValueError):
            website_results.WebsiteResultWriter(sheet)


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_saved_before_thumbnail_and_metadata_after_each_ad(self):
        row = SimpleNamespace(
            row_number=4, group_ad_name="CODE", ad_account_id="account", page_id="page",
            telegram_link="telegram", text_content="text", title="title", website_url="url",
            campaign_name="name", campaign_count=1, adset_count=1, ad_count=2,
            daily_budget=100000, pixel_id="pixel", schedule=None,
            age_min=None, age_max=None, genders=None,
        )
        events = []
        campaign_sheet, article_sheet = Mock(), Mock()
        writer = Mock()
        writer.write_upload.side_effect = lambda *args: events.append("save-upload")
        writer.write_posts.side_effect = lambda *args: events.append("save-post")
        telegram = Mock()
        telegram.__aenter__ = AsyncMock(return_value=telegram)
        telegram.__aexit__ = AsyncMock(return_value=False)
        telegram.download_video = AsyncMock(return_value="video.mp4")
        with ExitStack() as stack:
            def mocked(name, **kwargs):
                return stack.enter_context(patch.object(website_sales, name, **kwargs))
            mocked("load_dotenv")
            mocked("init_api")
            mocked("required_env", return_value="123")
            mocked("get_worksheet", side_effect=[campaign_sheet, article_sheet])
            mocked("read_website_assets", return_value={})
            mocked("read_website_sales_rows", return_value=[row])
            factory = mocked("WebsiteResultWriter", return_value=writer)
            mocked("TelegramDownloader", return_value=telegram)
            mocked("get_ad_account")
            mocked("upload_video", side_effect=lambda *args: events.append("upload") or "video")
            mocked("wait_for_video_thumbnail", side_effect=lambda *args: events.append("thumbnail") or "thumb")
            mocked("create_campaign", return_value={"id": "campaign"})
            mocked("create_adset", return_value={"id": "adset"})
            mocked("create_creative_from_video", return_value={"id": "creative"})
            mocked("create_ad", side_effect=lambda *args, **kwargs: events.append("ad") or {"id": "ad"})
            reads = mocked("read_post_once", side_effect=lambda *args: events.append("read") or ("", ""))
            result = mocked("write_result")
            await website_sales.run()
        factory.assert_called_once_with(article_sheet)
        writer.write_upload.assert_called_once_with("CODE", "video")
        self.assertEqual(events, ["upload", "save-upload", "thumbnail", "ad", "read", "save-post", "ad", "read", "save-post"])
        self.assertEqual(reads.call_count, 2)
        self.assertTrue(result.call_args.args[2].startswith("Thành công Website"))


if __name__ == "__main__":
    unittest.main()
