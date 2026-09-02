from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.sessions import StringSession


PRIVATE_LINK = re.compile(r"^/c/(?P<channel>\d+)/(?P<message>\d+)/?$")
PUBLIC_LINK = re.compile(r"^/(?P<username>[A-Za-z][A-Za-z0-9_]{3,})/(?P<message>\d+)/?$")


@dataclass(frozen=True)
class TelegramMessageRef:
    entity: int | str
    message_id: int


def parse_message_link(link: str) -> TelegramMessageRef:
    parsed = urlparse((link or "").strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "t.me",
        "www.t.me",
        "telegram.me",
    }:
        raise ValueError(f"Link Telegram không hợp lệ: {link}")

    private_match = PRIVATE_LINK.match(parsed.path)
    if private_match:
        channel_id = private_match.group("channel")
        return TelegramMessageRef(
            entity=int(f"-100{channel_id}"),
            message_id=int(private_match.group("message")),
        )

    public_match = PUBLIC_LINK.match(parsed.path)
    if public_match:
        return TelegramMessageRef(
            entity=public_match.group("username"),
            message_id=int(public_match.group("message")),
        )

    raise ValueError(
        "Chỉ hỗ trợ link dạng https://t.me/c/CHANNEL_ID/MESSAGE_ID "
        "hoặc https://t.me/USERNAME/MESSAGE_ID"
    )


class TelegramDownloader:
    def __init__(self, api_id: int, api_hash: str, session: str) -> None:
        self.client = TelegramClient(StringSession(session), api_id, api_hash)
        self._entities: dict[int | str, object] = {}

    async def __aenter__(self) -> "TelegramDownloader":
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("TELEGRAM_SESSION hết hạn hoặc chưa đăng nhập")
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.client.disconnect()

    async def download_video(self, link: str, destination: Path) -> Path:
        ref = parse_message_link(link)
        entity = self._entities.get(ref.entity)
        if entity is None:
            entity = await self.client.get_input_entity(ref.entity)
            self._entities[ref.entity] = entity
        message = await self.client.get_messages(entity, ids=ref.message_id)
        if not message:
            raise ValueError("Không tìm thấy tin nhắn Telegram hoặc tài khoản không có quyền xem")
        if not message.media:
            raise ValueError("Tin nhắn Telegram không chứa video/media")

        destination.mkdir(parents=True, exist_ok=True)
        downloaded = await self.client.download_media(message, file=str(destination))
        if not downloaded:
            raise RuntimeError("Telethon không tải được media")

        path = Path(downloaded)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("File video tải về bị trống")
        return path
