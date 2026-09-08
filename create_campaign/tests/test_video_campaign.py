import copy
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from src import cli, sheet_client, video_campaign as video


def row():
    return sheet_client.SheetRow(
        row_number=2, ad_account_id="account", page_id="page", campaign_name="Test",
        daily_budget=100000, post_id="old-post", campaign_count=2, adset_count=2,
        ad_count=3, group_ad_name="MDU1", schedule="2026-10-01T00:00:00+0700",
        age_min=25, age_max=55, genders=[2], message_template_name="template",
    )


class VideoCampaignTests(unittest.TestCase):
    def test_config_matches_workflow03_except_creative_source(self):
        before = copy.deepcopy(cli.SHEET_CAMPAIGN_TEMPLATE)
        with patch.object(cli, "get_template_json", return_value='{"text":"hello"}'):
            original = cli._build_campaign_configs_from_row(row())
            result = video.build_video_configs(row(), video.VideoAsset(2, "123", "title", "content"), "thumb")
        for old, new in zip(original, result):
            for old_set, new_set in zip(old["adsets"], new["adsets"]):
                for old_ad, new_ad in zip(old_set["ads"], new_set["ads"]):
                    self.assertNotIn("existing_post_id", new_ad)
                    self.assertEqual(new_ad["existing_video_id"], "123")
                    self.assertEqual(new_ad["title"], "title")
                    self.assertEqual(new_ad["message"], "content")
                    self.assertEqual(new_ad["page_welcome_message"], '{"text":"hello"}')
                    for key in ["existing_video_id", "title", "message", "thumbnail_url", "image_hash", "call_to_action", "destination"]:
                        new_ad.pop(key)
                    new_ad["existing_post_id"] = old_ad["existing_post_id"]
        self.assertEqual(original, result)
        self.assertEqual(before, cli.SHEET_CAMPAIGN_TEMPLATE)

    def test_video_payload_contains_title_message_messenger_and_template(self):
        account = Mock()
        with patch.object(cli, "get_template_json", return_value='{"text":"hello"}'), patch.object(cli, "create_creative_from_existing_post") as old:
            config = video.build_video_configs(row(), video.VideoAsset(2, "123", "title", "content"), "thumb")
            cli._build_creative(account, config[0]["adsets"][0]["ads"][0])
        old.assert_not_called()
        params = account.create_ad_creative.call_args.kwargs["params"]
        self.assertNotIn("object_story_id", params)
        self.assertEqual(params["page_welcome_message"], '{"text":"hello"}')
        self.assertEqual(params["object_story_spec"]["page_id"], "page")
        self.assertEqual(params["object_story_spec"]["video_data"], {
            "video_id": "123", "title": "title", "message": "content", "image_url": "thumb",
            "call_to_action": {"type": "MESSAGE_PAGE", "value": {"app_destination": "MESSENGER"}},
        })

    def test_read_assets_by_headers_and_reject_duplicate_codes(self):
        sheet = Mock()
        sheet.get_all_values.return_value = [
            ["Title", "Text_Content", "Mã", "FB_UPLOAD_ID"],
            ["title", "content", "MDU1", "1234567890123456789"],
        ]
        assets = video.read_video_assets(sheet)
        self.assertEqual(assets["MDU1"].video_id, "1234567890123456789")
        self.assertEqual(assets["MDU1"].row_number, 2)
        sheet.get_all_values.return_value.append(["title", "content", "MDU1", "456"])
        with self.assertRaises(ValueError):
            video.read_video_assets(sheet)

    def test_asset_validation(self):
        for asset in [video.VideoAsset(2, "", "t", "c"), video.VideoAsset(2, "[123]", "t", "c"), video.VideoAsset(2, "123", "", "c"), video.VideoAsset(2, "123", "t", "")]:
            with self.subTest(asset=asset), self.assertRaises(ValueError):
                video.require_asset({"MDU1": asset}, "MDU1")
        with self.assertRaises(ValueError):
            video.require_asset({}, "MDU1")

    def test_workflow12_does_not_require_post_column_but_03_still_does(self):
        headers = ["AD_ACCOUNT_ID", "PAGE_ID", "CAMPAIGN_NAME", "Mã", "DAILY_BUDGET", "SCHEDULE_DATE", "SCHEDULE_TIME", "CHAT_TEMPLATE", "Gender", "Age", "Camp_Structure", "RESULT"]
        sheet = Mock()
        sheet.get_all_values.return_value = [headers, ["account", "page", "name", "MDU1", "100000", "", "", "", "", "", "1-1-1", ""]]
        rows = sheet_client.read_rows(sheet, require_post_id=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].post_id, "")
        with self.assertRaises(ValueError):
            sheet_client.read_rows(sheet)
        headers.append("POST_ID")
        with patch.object(sheet_client, "write_result") as write:
            self.assertEqual(sheet_client.read_rows(sheet), [])
            self.assertIn("ID POST", write.call_args.args[2])

    def test_result_rows_are_skipped(self):
        headers = ["AD_ACCOUNT_ID", "PAGE_ID", "CAMPAIGN_NAME", "Mã", "DAILY_BUDGET", "SCHEDULE_DATE", "SCHEDULE_TIME", "CHAT_TEMPLATE", "Gender", "Age", "Camp_Structure", "RESULT"]
        sheet = Mock()
        sheet.get_all_values.return_value = [headers, ["a", "p", "n", "MDU1", "100000", "", "", "", "", "", "1-1-1", "done"]]
        self.assertEqual(sheet_client.read_rows(sheet, require_post_id=False), [])

    def test_execution_reuses_thumbnail_and_preserves_campaign_counts(self):
        r = row()
        r.message_template_name = None
        second = copy.copy(r)
        second.row_number = 3
        with ExitStack() as stack:
            def p(target, name, **kwargs):
                return stack.enter_context(patch.object(target, name, **kwargs))
            p(video, "load_dotenv")
            p(video, "init_api")
            p(sheet_client, "get_worksheet", side_effect=[Mock(), Mock()])
            reader = p(sheet_client, "read_rows", return_value=[r, second])
            p(video, "read_video_assets", return_value={"MDU1": video.VideoAsset(8, "123", "title", "content")})
            result_writer = p(video, "WebsiteResultWriter")
            thumbnail = p(video, "wait_for_video_thumbnail", return_value="thumb")
            account = p(video, "get_ad_account", return_value=Mock())
            process = p(video, "process_campaign_config", return_value={"campaign_id": "c", "adset_ids": ["s", "s"], "ad_ids": ["a", "a", "a"], "creative_ids": ["creative"]})
            post_reader = p(video, "read_post_once", return_value=("post", "link"))
            write = p(sheet_client, "write_result")
            video.main()
        self.assertFalse(reader.call_args.kwargs["require_post_id"])
        thumbnail.assert_called_once_with("123")
        account.assert_called_once()
        self.assertEqual(process.call_count, 4)
        self.assertEqual(post_reader.call_count, 4)
        result_writer.return_value.write_posts.assert_any_call(8, [("post", "link"), ("post", "link")])
        self.assertEqual(write.call_count, 2)
        self.assertIn("AdSet: 4, Ad: 6", write.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
