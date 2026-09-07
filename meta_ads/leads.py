"""Instant Forms (лід-форми) для цілі кампанії «Ліди» — збір заявок прямо в Facebook/
Instagram, без переходу на зовнішній сайт чи бота."""
from facebook_business.adobjects.leadgenform import LeadgenForm
from facebook_business.adobjects.page import Page

# Найпоширеніші стандартні поля форми. Повний список типів (напр. CITY, JOB_TITLE,
# COMPANY_NAME) — https://developers.facebook.com/docs/marketing-api/guides/lead-ads
DEFAULT_QUESTIONS = ("FULL_NAME", "PHONE")


def create_lead_form(
    page_id: str,
    name: str,
    privacy_policy_url: str,
    questions: list[str] | None = None,
) -> LeadgenForm:
    """Створює Instant Form на сторінці. `questions` — стандартні типи полів
    (за замовчуванням ім'я + телефон)."""
    page = Page(page_id)
    return page.create_lead_gen_form(params={
        "name": name,
        "questions": [{"type": q} for q in (questions or DEFAULT_QUESTIONS)],
        "privacy_policy": {"url": privacy_policy_url},
    })


def list_lead_forms(page_id: str) -> list[LeadgenForm]:
    page = Page(page_id)
    return list(page.get_lead_gen_forms(fields=[
        LeadgenForm.Field.id, LeadgenForm.Field.name, LeadgenForm.Field.status,
        LeadgenForm.Field.leads_count,
    ]))
