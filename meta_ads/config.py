"""Завантаження конфігурації зі змінних середовища (.env)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    app_id: str
    app_secret: str
    access_token: str
    ad_account_id: str  # у форматі act_<id>
    page_id: str | None  # Facebook-сторінка, від імені якої йдуть оголошення


def load_config() -> Config:
    missing = [
        name
        for name in ("META_APP_ID", "META_APP_SECRET", "META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID")
        if not (os.getenv(name) or "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Не вистачає змінних середовища: " + ", ".join(missing) + ". "
            "Скопіюйте .env.example у .env і заповніть їх — див. README.md, розділ "
            "«Отримання доступу до Meta Marketing API»."
        )

    ad_account_id = os.environ["META_AD_ACCOUNT_ID"].strip()
    if not ad_account_id.startswith("act_"):
        ad_account_id = f"act_{ad_account_id}"

    return Config(
        app_id=os.environ["META_APP_ID"].strip(),
        app_secret=os.environ["META_APP_SECRET"].strip(),
        access_token=os.environ["META_ACCESS_TOKEN"].strip(),
        ad_account_id=ad_account_id,
        page_id=(os.getenv("META_PAGE_ID") or "").strip() or None,
    )
