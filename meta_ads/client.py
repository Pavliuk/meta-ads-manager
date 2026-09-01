"""Ініціалізація Meta Marketing API SDK та отримання об'єкта рекламного акаунту."""
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.api import FacebookAdsApi

from meta_ads.config import Config, load_config

_initialized = False


def init_api(config: Config | None = None) -> Config:
    """Ініціалізує FacebookAdsApi (ідемпотентно) і повертає конфіг."""
    global _initialized
    config = config or load_config()
    if not _initialized:
        FacebookAdsApi.init(config.app_id, config.app_secret, config.access_token)
        _initialized = True
    return config


def get_ad_account(config: Config | None = None) -> AdAccount:
    config = init_api(config)
    return AdAccount(config.ad_account_id)
