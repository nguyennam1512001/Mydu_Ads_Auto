from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    google_sheet_id: str
    google_sheet_tab: str
    google_credentials: str
    fb_access_token: str
    fb_graph_version: str
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session: str

    @classmethod
    def from_env(cls, *, dry_run: bool = False) -> "Settings":
        required = ["GOOGLE_SHEET_ID", "GOOGLE_CREDENTIALS"]
        if not dry_run:
            required += [
                "FB_ACCESS_TOKEN",
                "TELEGRAM_API_ID",
                "TELEGRAM_API_HASH",
                "TELEGRAM_SESSION",
            ]

        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise EnvironmentError(f"Thiếu biến môi trường: {', '.join(missing)}")

        api_id_raw = os.getenv("TELEGRAM_API_ID", "0")
        try:
            api_id = int(api_id_raw)
        except ValueError as exc:
            raise ValueError("TELEGRAM_API_ID phải là số nguyên") from exc

        return cls(
            google_sheet_id=os.environ["GOOGLE_SHEET_ID"],
            google_sheet_tab=os.getenv("GOOGLE_SHEET_TAB", "Data"),
            google_credentials=os.environ["GOOGLE_CREDENTIALS"],
            fb_access_token=os.getenv("FB_ACCESS_TOKEN", ""),
            fb_graph_version=os.getenv("FB_GRAPH_VERSION", "v25.0"),
            telegram_api_id=api_id,
            telegram_api_hash=os.getenv("TELEGRAM_API_HASH", ""),
            telegram_session=os.getenv("TELEGRAM_SESSION", ""),
        )
