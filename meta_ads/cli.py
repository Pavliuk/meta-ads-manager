"""CLI для керування кампаніями Facebook/Instagram через Meta Marketing API.

Приклади:
    python -m meta_ads campaign create --name "Промо бота" --daily-budget 10
    python -m meta_ads adset create --campaign-id 12345 --name "UA 18-45" \\
        --daily-budget 10 --countries UA --interests 6003107902433
    python -m meta_ads ad create-creative --link https://t.me/my_bot?start=fb_ads \\
        --message "Персональний план тренувань за 2 хвилини" --headline "Спробуй безкоштовно" \\
        --page-id 123456789
    python -m meta_ads ad create --adset-id 67890 --name "Оголошення 1" --creative-id 111213
    python -m meta_ads insights show --id 12345 --level campaign
"""
import typer
from tabulate import tabulate

from meta_ads import adsets, ads, campaigns, insights, targeting

app = typer.Typer(help="Керування рекламою Facebook/Instagram (Meta Marketing API).")
campaign_app = typer.Typer(help="Кампанії")
adset_app = typer.Typer(help="Групи оголошень (ad sets)")
ad_app = typer.Typer(help="Креативи та оголошення")
insights_app = typer.Typer(help="Аналітика")
interests_app = typer.Typer(help="Пошук інтересів для таргетингу")

app.add_typer(campaign_app, name="campaign")
app.add_typer(adset_app, name="adset")
app.add_typer(ad_app, name="ad")
app.add_typer(insights_app, name="insights")
app.add_typer(interests_app, name="interests")


def _to_cents(amount: float) -> int:
    return int(round(amount * 100))


@campaign_app.command("create")
def campaign_create(
    name: str,
    objective: str = typer.Option("OUTCOME_TRAFFIC", help=f"Одна з: {', '.join(campaigns.OBJECTIVES)}"),
    daily_budget: float = typer.Option(None, help="Денний бюджет у валюті акаунту (напр. 10 = 10.00 USD)"),
    active: bool = typer.Option(False, "--active", help="Створити одразу ACTIVE (за замовчуванням PAUSED)"),
):
    campaign = campaigns.create_campaign(
        name=name,
        objective=objective,
        daily_budget_cents=_to_cents(daily_budget) if daily_budget is not None else None,
        status="ACTIVE" if active else "PAUSED",
    )
    typer.echo(f"✅ Створено кампанію id={campaign['id']} status={'ACTIVE' if active else 'PAUSED'}")


@campaign_app.command("list")
def campaign_list():
    rows = [
        [c["id"], c["name"], c["objective"], c["status"], c.get("daily_budget")]
        for c in campaigns.list_campaigns()
    ]
    typer.echo(tabulate(rows, headers=["id", "name", "objective", "status", "daily_budget"]))


@campaign_app.command("pause")
def campaign_pause(campaign_id: str):
    campaigns.set_campaign_status(campaign_id, "PAUSED")
    typer.echo(f"⏸️  Кампанію {campaign_id} призупинено.")


@campaign_app.command("resume")
def campaign_resume(campaign_id: str):
    campaigns.set_campaign_status(campaign_id, "ACTIVE")
    typer.echo(f"▶️  Кампанію {campaign_id} запущено.")


@adset_app.command("create")
def adset_create(
    campaign_id: str,
    name: str,
    daily_budget: float = typer.Option(..., help="Денний бюджет у валюті акаунту"),
    countries: str = typer.Option(..., help="Коди країн через кому, напр. UA,PL"),
    age_min: int = typer.Option(18),
    age_max: int = typer.Option(65),
    gender: str = typer.Option("all", help="all | male | female"),
    interests: str = typer.Option(None, help="ID інтересів через кому (див. `interests search`)"),
    platforms: str = typer.Option("facebook,instagram", help="facebook,instagram"),
    active: bool = typer.Option(False, "--active", help="Створити одразу ACTIVE"),
):
    spec = targeting.build_targeting(
        countries=[c.strip() for c in countries.split(",") if c.strip()],
        age_min=age_min,
        age_max=age_max,
        gender=gender,
        interest_ids=[i.strip() for i in interests.split(",") if i.strip()] if interests else None,
        platforms=[p.strip() for p in platforms.split(",") if p.strip()],
    )
    ad_set = adsets.create_ad_set(
        campaign_id=campaign_id,
        name=name,
        daily_budget_cents=_to_cents(daily_budget),
        targeting=spec,
        status="ACTIVE" if active else "PAUSED",
    )
    typer.echo(f"✅ Створено ad set id={ad_set['id']}")


@adset_app.command("list")
def adset_list(campaign_id: str):
    rows = [
        [a["id"], a["name"], a["status"], a.get("daily_budget"), a.get("optimization_goal")]
        for a in adsets.list_ad_sets(campaign_id)
    ]
    typer.echo(tabulate(rows, headers=["id", "name", "status", "daily_budget", "optimization_goal"]))


@adset_app.command("pause")
def adset_pause(ad_set_id: str):
    adsets.set_ad_set_status(ad_set_id, "PAUSED")
    typer.echo(f"⏸️  Ad set {ad_set_id} призупинено.")


@adset_app.command("resume")
def adset_resume(ad_set_id: str):
    adsets.set_ad_set_status(ad_set_id, "ACTIVE")
    typer.echo(f"▶️  Ad set {ad_set_id} запущено.")


@ad_app.command("create-creative")
def ad_create_creative(
    link: str,
    message: str,
    headline: str,
    page_id: str = typer.Option(None, help="За замовчуванням META_PAGE_ID з .env"),
    image: str = typer.Option(None, help="Шлях до локального файлу зображення"),
):
    image_hash = ads.upload_image(image) if image else None
    creative = ads.create_link_creative(
        link=link, message=message, headline=headline, page_id=page_id, image_hash=image_hash
    )
    typer.echo(f"✅ Створено креатив id={creative['id']}")


@ad_app.command("create")
def ad_create(
    ad_set_id: str,
    name: str,
    creative_id: str,
    active: bool = typer.Option(False, "--active", help="Створити одразу ACTIVE"),
):
    ad = ads.create_ad(ad_set_id=ad_set_id, name=name, creative_id=creative_id, status="ACTIVE" if active else "PAUSED")
    typer.echo(f"✅ Створено оголошення id={ad['id']}")


@ad_app.command("pause")
def ad_pause(ad_id: str):
    ads.set_ad_status(ad_id, "PAUSED")
    typer.echo(f"⏸️  Оголошення {ad_id} призупинено.")


@ad_app.command("resume")
def ad_resume(ad_id: str):
    ads.set_ad_status(ad_id, "ACTIVE")
    typer.echo(f"▶️  Оголошення {ad_id} запущено.")


@insights_app.command("show")
def insights_show(
    id: str = typer.Option(..., help="ID кампанії/ad set'а/оголошення"),
    level: str = typer.Option("campaign", help="campaign | adset | ad"),
    date_preset: str = typer.Option("last_7d"),
):
    rows = insights.get_insights(id, level=level, date_preset=date_preset)
    if not rows:
        typer.echo("Даних поки немає.")
        return
    typer.echo(tabulate(rows, headers="keys"))


@interests_app.command("search")
def interests_search(query: str, limit: int = 10):
    rows = [[r["id"], r["name"], r["audience_size"]] for r in targeting.search_interests(query, limit)]
    typer.echo(tabulate(rows, headers=["id", "name", "audience_size"]))


if __name__ == "__main__":
    app()
