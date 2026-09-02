"""Đọc mẫu tin nhắn Messenger (câu chào + quick reply) từ config/message_templates.yaml."""
import json
import os

import yaml

_TEMPLATES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config", "message_templates.yaml"
)


def get_template_json(name: str) -> str | None:
    """Tên mẫu (VD 'MDU Shop') -> chuỗi JSON dùng cho page_welcome_message. None nếu để trống."""
    name = (name or "").strip()
    if not name:
        return None

    if not os.path.exists(_TEMPLATES_PATH):
        raise FileNotFoundError(f"Không tìm thấy {_TEMPLATES_PATH}")

    with open(_TEMPLATES_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    templates = data.get("templates", {})
    if name not in templates:
        raise ValueError(
            f"Không tìm thấy mẫu tin nhắn '{name}' trong config/message_templates.yaml"
        )

    return json.dumps(templates[name], ensure_ascii=False)
