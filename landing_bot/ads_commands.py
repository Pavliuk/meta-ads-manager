"""Адмінські команди керування рекламою Facebook/Instagram прямо з Telegram.

Тонка обгортка над meta_ads (Campaign/AdSet/Ad/Insights) — той самий Meta
Marketing API, що й у CLI `python -m meta_ads`, просто викликається з чату
замість терміналу. Потребує META_APP_ID/META_APP_SECRET/META_ACCESS_TOKEN/
META_AD_ACCOUNT_ID (і META_PAGE_ID для креативів) у .env — без них команди
повертають зрозумілу помилку замість падіння.

Синтаксис багатоаргументних команд — через " | " (вертикальну риску), бо
назви кампаній/ad set'ів можуть містити пробіли:
    /campaign_new Промо бота | 10
"""
import asyncio
import tempfile

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message
from facebook_business.exceptions import FacebookRequestError
from tabulate import tabulate

from meta_ads import adsets, ads, campaigns, insights, targeting
from meta_ads.config import load_config as load_meta_config

router = Router(name="ads_commands")

HELP_TEXT = (
    "📣 <b>Керування рекламою</b>\n\n"
    "<b>Кампанії</b>\n"
    "/campaign_new назва | бюджет_на_день — створити (PAUSED)\n"
    "/campaigns — список\n"
    "/campaign_pause id\n"
    "/campaign_resume id\n\n"
    "<b>Ad set'и</b>\n"
    "/adset_new campaign_id | назва | бюджет_на_день | країни(UA,PL) — створити (PAUSED)\n"
    "/adsets campaign_id — список\n"
    "/adset_pause id\n"
    "/adset_resume id\n\n"
    "<b>Креатив і оголошення</b>\n"
    "/creative_new посилання | текст | заголовок — можна прикріпити фото до повідомлення\n"
    "/ad_new adset_id | creative_id | назва — створити (PAUSED)\n"
    "/ad_pause id\n"
    "/ad_resume id\n\n"
    "<b>Аналітика</b>\n"
    "/insights object_id [campaign|adset|ad] [date_preset]\n"
    "  напр. /insights 12345 adset last_30d (за замовчуванням campaign, last_7d)"
)


def _is_admin(message: Message, admin_ids: list[int]) -> bool:
    return message.from_user is not None and message.from_user.id in admin_ids


def _split_args(text: str, n: int) -> list[str] | None:
    parts = [p.strip() for p in text.split("|")]
    return parts if len(parts) == n and all(parts) else None


async def _meta_call(tg_message: Message, func, /, *args, **kwargs):
    """Виконує блокуючий виклик meta_ads у потоці, з людяними повідомленнями про помилки.

    tg_message приймається лише позиційно — щоб не конфліктувати з kwarg'ом
    `message=` (текст оголошення), який деякі meta_ads-функції приймають під тим
    самим ім'ям.
    """
    try:
        load_meta_config()
    except RuntimeError as e:
        await tg_message.answer(f"⚠️ {e}")
        return None
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except FacebookRequestError as e:
        await tg_message.answer(f"❌ Meta API: {e.api_error_message()}")
        return None


@router.message(Command("ads_help"))
async def cmd_ads_help(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    await message.answer(HELP_TEXT)


@router.message(Command("campaign_new"))
async def cmd_campaign_new(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    rest = (message.text or "").split(maxsplit=1)
    args = _split_args(rest[1], 2) if len(rest) > 1 else None
    if not args:
        await message.answer("Формат: /campaign_new назва | бюджет_на_день")
        return
    name, budget_str = args
    try:
        budget_cents = int(round(float(budget_str.replace(",", ".")) * 100))
    except ValueError:
        await message.answer("Бюджет має бути числом, напр. 10")
        return
    campaign = await _meta_call(message, campaigns.create_campaign, name=name, daily_budget_cents=budget_cents)
    if campaign:
        await message.answer(f"✅ Кампанія id={campaign['id']} status=PAUSED")


@router.message(Command("campaigns"))
async def cmd_campaigns(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    rows = await _meta_call(message, campaigns.list_campaigns)
    if rows is None:
        return
    if not rows:
        await message.answer("Кампаній ще немає.")
        return
    table = tabulate(
        [[c["id"], c["name"], c["status"], c.get("daily_budget")] for c in rows],
        headers=["id", "назва", "статус", "бюджет"],
    )
    await message.answer(f"<pre>{table}</pre>")


@router.message(Command("campaign_pause"))
async def cmd_campaign_pause(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /campaign_pause id")
        return
    if await _meta_call(message, campaigns.set_campaign_status, parts[1], "PAUSED") is not None:
        await message.answer(f"⏸️ Кампанію {parts[1]} призупинено.")


@router.message(Command("campaign_resume"))
async def cmd_campaign_resume(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /campaign_resume id")
        return
    if await _meta_call(message, campaigns.set_campaign_status, parts[1], "ACTIVE") is not None:
        await message.answer(f"▶️ Кампанію {parts[1]} запущено.")


@router.message(Command("adset_new"))
async def cmd_adset_new(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    rest = (message.text or "").split(maxsplit=1)
    args = _split_args(rest[1], 4) if len(rest) > 1 else None
    if not args:
        await message.answer("Формат: /adset_new campaign_id | назва | бюджет_на_день | країни(UA,PL)")
        return
    campaign_id, name, budget_str, countries_str = args
    try:
        budget_cents = int(round(float(budget_str.replace(",", ".")) * 100))
    except ValueError:
        await message.answer("Бюджет має бути числом, напр. 10")
        return
    countries = [c.strip().upper() for c in countries_str.split(",") if c.strip()]
    if not countries:
        await message.answer("Вкажіть хоча б одну країну, напр. UA")
        return
    spec = targeting.build_targeting(countries=countries)
    ad_set = await _meta_call(
        message, adsets.create_ad_set, campaign_id=campaign_id, name=name,
        daily_budget_cents=budget_cents, targeting=spec,
    )
    if ad_set:
        await message.answer(f"✅ Ad set id={ad_set['id']} status=PAUSED")


@router.message(Command("adsets"))
async def cmd_adsets(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /adsets campaign_id")
        return
    rows = await _meta_call(message, adsets.list_ad_sets, parts[1])
    if rows is None:
        return
    if not rows:
        await message.answer("Ad set'ів ще немає в цій кампанії.")
        return
    table = tabulate(
        [[a["id"], a["name"], a["status"], a.get("daily_budget")] for a in rows],
        headers=["id", "назва", "статус", "бюджет"],
    )
    await message.answer(f"<pre>{table}</pre>")


@router.message(Command("adset_pause"))
async def cmd_adset_pause(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /adset_pause id")
        return
    if await _meta_call(message, adsets.set_ad_set_status, parts[1], "PAUSED") is not None:
        await message.answer(f"⏸️ Ad set {parts[1]} призупинено.")


@router.message(Command("adset_resume"))
async def cmd_adset_resume(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /adset_resume id")
        return
    if await _meta_call(message, adsets.set_ad_set_status, parts[1], "ACTIVE") is not None:
        await message.answer(f"▶️ Ad set {parts[1]} запущено.")


@router.message(Command("creative_new"))
async def cmd_creative_new(message: Message, admin_ids: list[int], bot: Bot):
    if not _is_admin(message, admin_ids):
        return
    raw = message.caption if message.photo else message.text
    rest = (raw or "").split(maxsplit=1)
    args = _split_args(rest[1], 3) if len(rest) > 1 else None
    if not args:
        await message.answer("Формат: /creative_new посилання | текст | заголовок (можна прикріпити фото)")
        return
    link, text, headline = args

    image_hash = None
    if message.photo:
        try:
            load_meta_config()
        except RuntimeError as e:
            await message.answer(f"⚠️ {e}")
            return
        file = await bot.get_file(message.photo[-1].file_id)
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            await bot.download_file(file.file_path, tmp.name)
            image_hash = await _meta_call(message, ads.upload_image, tmp.name)
            if image_hash is None:
                return

    creative = await _meta_call(
        message, ads.create_link_creative, link=link, message=text, headline=headline, image_hash=image_hash,
    )
    if creative:
        await message.answer(f"✅ Креатив id={creative['id']}")


@router.message(Command("ad_new"))
async def cmd_ad_new(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    rest = (message.text or "").split(maxsplit=1)
    args = _split_args(rest[1], 3) if len(rest) > 1 else None
    if not args:
        await message.answer("Формат: /ad_new adset_id | creative_id | назва")
        return
    adset_id, creative_id, name = args
    ad = await _meta_call(message, ads.create_ad, ad_set_id=adset_id, name=name, creative_id=creative_id)
    if ad:
        await message.answer(f"✅ Оголошення id={ad['id']} status=PAUSED")


@router.message(Command("ad_pause"))
async def cmd_ad_pause(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /ad_pause id")
        return
    if await _meta_call(message, ads.set_ad_status, parts[1], "PAUSED") is not None:
        await message.answer(f"⏸️ Оголошення {parts[1]} призупинено.")


@router.message(Command("ad_resume"))
async def cmd_ad_resume(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /ad_resume id")
        return
    if await _meta_call(message, ads.set_ad_status, parts[1], "ACTIVE") is not None:
        await message.answer(f"▶️ Оголошення {parts[1]} запущено.")


@router.message(Command("insights"))
async def cmd_insights(message: Message, admin_ids: list[int]):
    if not _is_admin(message, admin_ids):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /insights object_id [campaign|adset|ad] [date_preset]")
        return
    object_id = parts[1]
    level = parts[2] if len(parts) > 2 else "campaign"
    date_preset = parts[3] if len(parts) > 3 else "last_7d"
    rows = await _meta_call(message, insights.get_insights, object_id, level, date_preset)
    if rows is None:
        return
    if not rows:
        await message.answer("Даних поки немає.")
        return
    await message.answer(f"<pre>{tabulate(rows, headers='keys')}</pre>")
