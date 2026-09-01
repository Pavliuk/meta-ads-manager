"""CRUD-операції над рекламними кампаніями (Campaign)."""
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign

from meta_ads.client import get_ad_account

# Найпоширеніші цілі кампанії (Meta Marketing API v21+, формат OUTCOME_*).
# Повний список: https://developers.facebook.com/docs/marketing-api/reference/ad-campaign-group/
OBJECTIVES = (
    "OUTCOME_TRAFFIC",       # трафік на посилання/сайт — типова ціль для "залити трафік"
    "OUTCOME_ENGAGEMENT",    # взаємодія з постом/сторінкою
    "OUTCOME_LEADS",         # ліди (форми, повідомлення)
    "OUTCOME_AWARENESS",     # охоплення/впізнаваність
    "OUTCOME_SALES",         # конверсії/продажі
    "OUTCOME_APP_PROMOTION",
)


def create_campaign(
    name: str,
    objective: str = "OUTCOME_TRAFFIC",
    daily_budget_cents: int | None = None,
    status: str = "PAUSED",
    account: AdAccount | None = None,
) -> Campaign:
    """Створює кампанію. За замовчуванням PAUSED — свідомо вмикайте показ окремо,
    коли перевірите ad set/оголошення."""
    if objective not in OBJECTIVES:
        raise ValueError(f"objective має бути одним з {OBJECTIVES}, отримано {objective!r}")

    account = account or get_ad_account()
    params = {
        Campaign.Field.name: name,
        Campaign.Field.objective: objective,
        Campaign.Field.status: status,
        Campaign.Field.special_ad_categories: [],
    }
    if daily_budget_cents is not None:
        params[Campaign.Field.daily_budget] = daily_budget_cents

    return account.create_campaign(params=params)


def list_campaigns(account: AdAccount | None = None) -> list[Campaign]:
    account = account or get_ad_account()
    fields = [
        Campaign.Field.id,
        Campaign.Field.name,
        Campaign.Field.objective,
        Campaign.Field.status,
        Campaign.Field.daily_budget,
    ]
    return list(account.get_campaigns(fields=fields))


def set_campaign_status(campaign_id: str, status: str) -> Campaign:
    """status: ACTIVE | PAUSED | ARCHIVED | DELETED"""
    campaign = Campaign(campaign_id)
    campaign.api_update(params={Campaign.Field.status: status})
    return campaign
