import unittest

from src.telegram_client import parse_message_link


class TelegramLinkTests(unittest.TestCase):
    def test_private_channel_link(self) -> None:
        ref = parse_message_link("https://t.me/c/3488855350/11114")
        self.assertEqual(ref.entity, -1003488855350)
        self.assertEqual(ref.message_id, 11114)

    def test_public_channel_link(self) -> None:
        ref = parse_message_link("https://t.me/example_channel/25")
        self.assertEqual(ref.entity, "example_channel")
        self.assertEqual(ref.message_id, 25)

    def test_invalid_link(self) -> None:
        with self.assertRaises(ValueError):
            parse_message_link("https://example.com/video.mp4")


if __name__ == "__main__":
    unittest.main()

