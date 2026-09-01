"""Отримання статистики (Insights) по кампаніях/ad set'ах/оголошеннях."""
from facebook_business.adobjects.abstractobject import AbstractObject
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.campaign import Campaign

DEFAULT_FIELDS = [
    "campaign_name",
    "spend",
    "impressions",
    "clicks",
    "ctr",
    "cpc",
    "actions",
]

_LEVEL_CLASSES = {"campaign": Campaign, "adset": AdSet, "ad": Ad}


def get_insights(
    object_id: str,
    level: str = "campaign",
    date_preset: str = "last_7d",
    fields: list[str] | None = None,
) -> list[dict]:
    """level: campaign | adset | ad. date_preset: today, yesterday, last_7d, last_30d тощо
    (повний список — https://developers.facebook.com/docs/marketing-api/insights/parameters)."""
    if level not in _LEVEL_CLASSES:
        raise ValueError(f"level має бути одним з {list(_LEVEL_CLASSES)}, отримано {level!r}")

    obj: AbstractObject = _LEVEL_CLASSES[level](object_id)
    result = obj.get_insights(
        fields=fields or DEFAULT_FIELDS,
        params={"date_preset": date_preset, "level": level},
    )
    return [dict(row) for row in result]
