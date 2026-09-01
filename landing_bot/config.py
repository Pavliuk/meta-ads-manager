"""Завантаження конфігурації лендинг-бота зі змінних середовища (.env)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    bot_token: str
    admin_ids: list[int]


def _parse_admin_ids(raw: str) -> list[int]:
    return [int(chunk.strip()) for chunk in raw.split(",") if chunk.strip().isdigit()]


def load_config() -> Config:
    token = (os.getenv("LANDING_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "Не знайдено LANDING_BOT_TOKEN. Скопіюйте .env.example у .env і вкажіть "
            "токен бота, отриманий у @BotFather."
        )
    return Config(
        bot_token=token,
        admin_ids=_parse_admin_ids(os.getenv("ADMIN_IDS", "")),
    )
