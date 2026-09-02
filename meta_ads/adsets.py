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
        AdSet.Field.bid_strategy: AdSet.BidStrategy.lowest_cost_without_cap,
        AdSet.Field.targeting: targeting,
        AdSet.Field.status: status,
    }
    return account.create_ad_set(params=params)


def get_ad_set(ad_set_id: str) -> AdSet:
    """Пряме отримання одного ad set'а за ID (включно з campaign_id — без списку всіх)."""
    ad_set = AdSet(ad_set_id)
    ad_set.api_get(fields=[
        AdSet.Field.id,
        AdSet.Field.name,
        AdSet.Field.status,
        AdSet.Field.daily_budget,
        AdSet.Field.optimization_goal,
        AdSet.Field.campaign_id,
    ])
    return ad_set


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


def update_ad_set(
    ad_set_id: str,
    name: str | None = None,
    daily_budget_cents: int | None = None,
) -> AdSet:
    """Змінює назву та/або денний бюджет наявного ad set'а."""
    params = {}
    if name is not None:
        params[AdSet.Field.name] = name
    if daily_budget_cents is not None:
        params[AdSet.Field.daily_budget] = daily_budget_cents
    ad_set = AdSet(ad_set_id)
    if params:
        ad_set.api_update(params=params)
    return ad_set


def duplicate_ad_set(ad_set_id: str, deep_copy: bool = True) -> str:
    """Дублює ad set (типово разом з оголошеннями) у тій самій кампанії, статус PAUSED.
    Повертає ID нового ad set'а."""
    ad_set = AdSet(ad_set_id)
    result = ad_set.create_copy(params={
        "deep_copy": deep_copy,
        "status_option": AdSet.StatusOption.paused,
    })
    new_id = result.get("copied_adset_id") or result.get(AdSet.Field.id)
    if not new_id:
        raise RuntimeError("Meta API не повернув ID копії ad set'а.")
    return new_id
