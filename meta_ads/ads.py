"""Створення рекламного креативу (AdCreative) та оголошення (Ad)."""
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.adimage import AdImage

from meta_ads.client import get_ad_account
from meta_ads.config import Config, load_config


def upload_image(image_path: str, account: AdAccount | None = None) -> str:
    """Завантажує зображення в бібліотеку акаунту, повертає image_hash для креативу."""
    account = account or get_ad_account()
    image = AdImage(parent_id=account.get_id())
    image[AdImage.Field.filename] = image_path
    image.remote_create()
    return image[AdImage.Field.hash]


def create_link_creative(
    link: str,
    message: str,
    headline: str,
    page_id: str | None = None,
    image_hash: str | None = None,
    config: Config | None = None,
    account: AdAccount | None = None,
) -> AdCreative:
    """Креатив для посилання (типовий для трафіку на лендінг/бота): текст + заголовок + картинка."""
    config = config or load_config()
    page_id = page_id or config.page_id
    if not page_id:
        raise ValueError(
            "Не вказано page_id — потрібна Facebook-сторінка, від імені якої йде оголошення "
            "(параметр page_id або META_PAGE_ID у .env)."
        )
    account = account or get_ad_account()

    link_data = {
        "link": link,
        "message": message,
        "name": headline,
    }
    if image_hash:
        link_data["image_hash"] = image_hash

    params = {
        AdCreative.Field.name: headline,
        AdCreative.Field.object_story_spec: {
            "page_id": page_id,
            "link_data": link_data,
        },
    }
    return account.create_ad_creative(params=params)


def create_ad(
    ad_set_id: str,
    name: str,
    creative_id: str,
    status: str = "PAUSED",
    account: AdAccount | None = None,
) -> Ad:
    account = account or get_ad_account()
    params = {
        Ad.Field.name: name,
        Ad.Field.adset_id: ad_set_id,
        Ad.Field.creative: {"creative_id": creative_id},
        Ad.Field.status: status,
    }
    return account.create_ad(params=params)


def set_ad_status(ad_id: str, status: str) -> Ad:
    """status: ACTIVE | PAUSED | ARCHIVED | DELETED"""
    ad = Ad(ad_id)
    ad.api_update(params={Ad.Field.status: status})
    return ad


def get_ad(ad_id: str) -> Ad:
    """Пряме отримання одного оголошення за ID (включно з adset_id — без списку всіх)."""
    ad = Ad(ad_id)
    ad.api_get(fields=[Ad.Field.id, Ad.Field.name, Ad.Field.status, Ad.Field.adset_id])
    return ad


def update_ad(ad_id: str, name: str) -> Ad:
    """Перейменовує оголошення."""
    ad = Ad(ad_id)
    ad.api_update(params={Ad.Field.name: name})
    return ad


def list_ads(ad_set_id: str, account: AdAccount | None = None) -> list[Ad]:
    account = account or get_ad_account()
    fields = [Ad.Field.id, Ad.Field.name, Ad.Field.status]
    return list(account.get_ads(fields=fields, params={"filtering": [{
        "field": "adset.id", "operator": "EQUAL", "value": ad_set_id,
    }]}))
