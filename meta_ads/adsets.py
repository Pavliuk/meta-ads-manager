"""CRUD-операції над групами оголошень (AdSet) — бюджет, розклад, таргетинг."""
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adset import AdSet

from meta_ads.client import get_ad_account


def create_ad_set(
    campaign_id: str,
    name: str,
    daily_budget_cents: int,
    targeting: dict,
    optimization_goal: str = "LINK_CLICKS",
    billing_event: str = "IMPRESSIONS",
    status: str = "PAUSED",
    account: AdAccount | None = None,
) -> AdSet:
    account = account or get_ad_account()
    params = {
        AdSet.Field.name: name,
        AdSet.Field.campaign_id: campaign_id,
        AdSet.Field.daily_budget: daily_budget_cents,
        AdSet.Field.billing_event: billing_event,
        AdSet.Field.optimization_goal: optimization_goal,
        AdSet.Field.targeting: targeting,
        AdSet.Field.status: status,
    }
    return account.create_ad_set(params=params)


def list_ad_sets(campaign_id: str, account: AdAccount | None = None) -> list[AdSet]:
    account = account or get_ad_account()
    fields = [
        AdSet.Field.id,
        AdSet.Field.name,
        AdSet.Field.status,
        AdSet.Field.daily_budget,
        AdSet.Field.optimization_goal,
    ]
    return list(account.get_ad_sets(fields=fields, params={"filtering": [{
        "field": "campaign.id", "operator": "EQUAL", "value": campaign_id,
    }]}))


def set_ad_set_status(ad_set_id: str, status: str) -> AdSet:
    """status: ACTIVE | PAUSED | ARCHIVED | DELETED"""
    ad_set = AdSet(ad_set_id)
    ad_set.api_update(params={AdSet.Field.status: status})
    return ad_set
