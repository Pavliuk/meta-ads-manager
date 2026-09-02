"""Кнопкове (inline-keyboard) керування рекламою Facebook/Instagram з Telegram.

Той самий Meta Marketing API, що й `python -m meta_ads`, тільки через меню
замість команд із синтаксисом. Створення/редагування/видалення/дублювання
кампанії, ad set'а чи оголошення — короткий покроковий діалог (FSM): бот
питає одне поле за раз, з кнопкою «Скасувати» на кожному кроці; видалення —
з окремим підтвердженням.

Кожен рівень (кампанія/ad set/оголошення) отримується напряму за власним ID
(без списку всіх і фільтрації) — тому в callback_data передається лише ID
поточного об'єкта, а не весь ланцюжок батьківських ID (це і коротше — в межах
64-байтного ліміту Telegram на callback_data, — і дешевше за кількістю
запитів до Meta API).

Потребує META_APP_ID/META_APP_SECRET/META_ACCESS_TOKEN/META_AD_ACCOUNT_ID
(і META_PAGE_ID для оголошень) у .env — без них дії повертають зрозуміле
попередження замість падіння.
"""
import asyncio
import logging
import tempfile

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, User
from aiogram.utils.keyboard import InlineKeyboardBuilder
from facebook_business.exceptions import FacebookRequestError
from tabulate import tabulate

from meta_ads import adsets, ads, campaigns, client, insights, targeting
from meta_ads.campaigns import OBJECTIVES
from meta_ads.config import load_config as load_meta_config

router = Router(name="ads_commands")
logger = logging.getLogger(__name__)

DATE_PRESETS = ("today", "last_7d", "last_30d", "last_90d")
DATE_PRESET_LABELS = {"today": "Сьогодні", "last_7d": "7 днів", "last_30d": "30 днів", "last_90d": "90 днів"}

OBJECTIVE_LABELS = {
    "OUTCOME_TRAFFIC": "🔗 Трафік",
    "OUTCOME_ENGAGEMENT": "💬 Взаємодія",
    "OUTCOME_LEADS": "🎯 Ліди",
    "OUTCOME_AWARENESS": "📢 Охоплення",
    "OUTCOME_SALES": "💰 Конверсії",
    "OUTCOME_APP_PROMOTION": "📱 Застосунок",
}

GENDER_LABELS = {"all": "👥 Всі", "male": "👨 Чоловіки", "female": "👩 Жінки"}
PLATFORM_LABELS = {"both": "📘📷 FB + IG", "facebook": "📘 Тільки Facebook", "instagram": "📷 Тільки Instagram"}
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "UAH": "₴", "GBP": "£", "PLN": "zł"}

_currency_cache: dict[str, str] = {}


class AdsFlow(StatesGroup):
    campaign_name = State()
    campaign_budget = State()
    campaign_objective = State()
    campaign_edit_name = State()
    campaign_edit_budget = State()

    adset_name = State()
    adset_budget = State()
    adset_countries = State()
    adset_age_min = State()
    adset_age_max = State()
    adset_gender = State()
    adset_platforms = State()
    adset_interests = State()
    adset_interest_query = State()
    adset_interest_pick = State()
    adset_edit_name = State()
    adset_edit_budget = State()

    ad_link = State()
    ad_message = State()
    ad_headline = State()
    ad_photo = State()
    ad_edit_name = State()


# ---------- Допоміжне ----------

def _is_admin(user: User | None, admin_ids: list[int]) -> bool:
    return user is not None and user.id in admin_ids


def _cancel_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Скасувати", callback_data="ads:cancel")
    return kb.as_markup()


def _menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Кампанії", callback_data="ads:camps")
    kb.button(text="➕ Нова кампанія", callback_data="ads:new_campaign")
    kb.button(text="💳 Акаунт", callback_data="ads:account")
    kb.adjust(1)
    return kb.as_markup()


async def _meta_call(send, func, /, *args, **kwargs):
    """Виконує блокуючий виклик meta_ads у потоці; send(text) — як повідомити про помилку."""
    try:
        load_meta_config()
    except RuntimeError as e:
        await send(f"⚠️ {e}")
        return None
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except FacebookRequestError as e:
        logger.error(
            "Meta API error: type=%s code=%s subcode=%s message=%s blame_field_specs=%s body=%s",
            e.api_error_type(), e.api_error_code(), e.api_error_subcode(), e.api_error_message(),
            e.api_blame_field_specs(), e.body(),
        )
        await send(
            f"❌ Meta API [{e.api_error_code()}/{e.api_error_subcode()}]: {e.api_error_message()}"
        )
        return None
    except RuntimeError as e:
        await send(f"❌ {e}")
        return None


def _status_icon(status: str) -> str:
    return "🟢" if status == "ACTIVE" else "⏸️"


def _parse_budget(text: str) -> float | None:
    try:
        value = float((text or "").replace(",", "."))
    except ValueError:
        return None
    return value if value > 0 else None


def _parse_age(text: str) -> int | None:
    try:
        value = int((text or "").strip())
    except ValueError:
        return None
    return value if 13 <= value <= 65 else None


async def _get_currency() -> str:
    """Валюта рекламного акаунту, з кешем на весь час роботи процесу."""
    if "value" not in _currency_cache:
        try:
            info = await asyncio.to_thread(client.get_account_info)
            _currency_cache["value"] = info.get("currency") or "USD"
        except Exception:
            _currency_cache["value"] = "USD"
    return _currency_cache["value"]


def _fmt_money(raw, currency: str) -> str:
    """Meta повертає бюджети/витрати в мінімальних одиницях валюти (напр. центах)."""
    if raw in (None, ""):
        return "—"
    try:
        amount = int(raw) / 100
    except (TypeError, ValueError):
        return str(raw)
    symbol = CURRENCY_SYMBOLS.get(currency)
    return f"{symbol}{amount:.2f}" if symbol else f"{amount:.2f} {currency}"


# ---------- Головне меню ----------

@router.message(Command("ads"))
async def cmd_ads_menu(message: Message, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    await message.answer("📣 <b>Керування рекламою</b>\nОберіть дію:", reply_markup=_menu_kb())


@router.callback_query(F.data == "ads:menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    await state.clear()
    await callback.message.edit_text("📣 <b>Керування рекламою</b>\nОберіть дію:", reply_markup=_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "ads:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        "Скасовано.\n\n📣 <b>Керування рекламою</b>\nОберіть дію:", reply_markup=_menu_kb()
    )
    await callback.answer()


# ---------- Акаунт ----------

@router.callback_query(F.data == "ads:account")
async def cb_account(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()

    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    info = await _meta_call(send, client.get_account_info)
    await callback.answer()
    if info is None:
        return
    currency = info.get("currency") or "USD"
    text = (
        f"<b>💳 {info.get('name') or '—'}</b>\n"
        f"Валюта: {currency}\n"
        f"Витрачено (усього): {_fmt_money(info.get('amount_spent'), currency)}\n"
        f"Баланс: {_fmt_money(info.get('balance'), currency)}"
    )
    if info.get("spend_cap"):
        text += f"\nЛіміт витрат: {_fmt_money(info.get('spend_cap'), currency)}"
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Меню", callback_data="ads:menu")
    kb.adjust(1)
    await callback.message.edit_text(text, reply_markup=kb.as_markup())


# ---------- Кампанії ----------

async def _render_campaigns(callback: CallbackQuery):
    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    rows = await _meta_call(send, campaigns.list_campaigns)
    if rows is None:
        return
    if not rows:
        await callback.message.edit_text("Кампаній ще немає.", reply_markup=_menu_kb())
        return
    kb = InlineKeyboardBuilder()
    for c in rows:
        kb.button(text=f"{_status_icon(c['status'])} {c['name']}", callback_data=f"ads:camp:{c['id']}")
    kb.button(text="🔙 Меню", callback_data="ads:menu")
    kb.adjust(1)
    await callback.message.edit_text("<b>Кампанії:</b>", reply_markup=kb.as_markup())


@router.callback_query(F.data == "ads:camps")
async def cb_campaigns(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    await _render_campaigns(callback)
    await callback.answer()


async def _render_campaign_detail(callback: CallbackQuery, campaign_id: str):
    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    campaign = await _meta_call(send, campaigns.get_campaign, campaign_id)
    if campaign is None:
        return
    currency = await _get_currency()

    text = (
        f"<b>{campaign['name']}</b>\n"
        f"Статус: {campaign['status']}\n"
        f"Ціль: {campaign.get('objective') or '—'}\n"
        f"Бюджет/день: {_fmt_money(campaign.get('daily_budget'), currency)}"
    )
    kb = InlineKeyboardBuilder()
    if campaign["status"] == "ACTIVE":
        kb.button(text="⏸️ Пауза", callback_data=f"ads:camp_pause:{campaign_id}")
    else:
        kb.button(text="▶️ Запустити", callback_data=f"ads:camp_resume:{campaign_id}")
    kb.button(text="🎯 Ad sets", callback_data=f"ads:adsets:{campaign_id}")
    kb.button(text="📊 Аналітика", callback_data=f"ads:ins:campaign:{campaign_id}")
    kb.button(text="✏️ Редагувати", callback_data=f"ads:camp_edit:{campaign_id}")
    kb.button(text="📄 Дублювати", callback_data=f"ads:camp_dup:{campaign_id}")
    kb.button(text="🗑 Видалити", callback_data=f"ads:camp_del_ask:{campaign_id}")
    kb.button(text="🔙 До списку", callback_data="ads:camps")
    kb.adjust(1)
    await callback.message.edit_text(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("ads:camp:"))
async def cb_campaign_detail(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    campaign_id = callback.data.split(":")[2]
    await _render_campaign_detail(callback, campaign_id)
    await callback.answer()


@router.callback_query(F.data.startswith("ads:camp_pause:") | F.data.startswith("ads:camp_resume:"))
async def cb_campaign_toggle(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    action, campaign_id = callback.data.split(":")[1], callback.data.split(":")[2]
    status = "ACTIVE" if action == "camp_resume" else "PAUSED"

    async def send(text):
        await callback.answer(text, show_alert=True)

    result = await _meta_call(send, campaigns.set_campaign_status, campaign_id, status)
    if result is None:
        return
    await callback.answer("✅ Готово")
    await _render_campaign_detail(callback, campaign_id)


@router.callback_query(F.data.startswith("ads:camp_dup:"))
async def cb_campaign_duplicate(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    campaign_id = callback.data.split(":")[2]

    async def send(text):
        await callback.answer(text, show_alert=True)

    new_id = await _meta_call(send, campaigns.duplicate_campaign, campaign_id)
    if new_id is None:
        return
    await callback.answer("✅ Продубльовано")
    await _render_campaign_detail(callback, new_id)


# --- Видалення кампанії ---

@router.callback_query(F.data.startswith("ads:camp_del_ask:"))
async def cb_campaign_delete_ask(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    campaign_id = callback.data.split(":")[2]
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Так, видалити", callback_data=f"ads:camp_del:{campaign_id}")
    kb.button(text="❌ Ні", callback_data=f"ads:camp:{campaign_id}")
    kb.adjust(1)
    await callback.message.edit_text(
        "⚠️ Видалити цю кампанію разом з усіма її ad set'ами й оголошеннями? Це незворотньо.",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ads:camp_del:"))
async def cb_campaign_delete(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    campaign_id = callback.data.split(":")[2]

    async def send(text):
        await callback.answer(text, show_alert=True)

    result = await _meta_call(send, campaigns.set_campaign_status, campaign_id, "DELETED")
    if result is None:
        return
    await callback.answer("🗑 Видалено")
    await _render_campaigns(callback)


# --- Редагування кампанії ---

@router.callback_query(F.data.startswith("ads:camp_edit:"))
async def cb_campaign_edit_menu(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    campaign_id = callback.data.split(":")[2]
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Назва", callback_data=f"ads:camp_edit_name:{campaign_id}")
    kb.button(text="💰 Бюджет", callback_data=f"ads:camp_edit_budget:{campaign_id}")
    kb.button(text="🔙 Назад", callback_data=f"ads:camp:{campaign_id}")
    kb.adjust(1)
    await callback.message.edit_text("Що редагувати?", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("ads:camp_edit_name:"))
async def cb_campaign_edit_name_ask(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    campaign_id = callback.data.split(":")[2]
    await state.update_data(edit_campaign_id=campaign_id)
    await state.set_state(AdsFlow.campaign_edit_name)
    await callback.message.edit_text("Нова назва кампанії:", reply_markup=_cancel_kb())
    await callback.answer()


@router.message(AdsFlow.campaign_edit_name)
async def fsm_campaign_edit_name(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("Назва не може бути порожньою. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    data = await state.get_data()
    campaign_id = data["edit_campaign_id"]
    await state.clear()

    async def send(text):
        await message.answer(text, reply_markup=_menu_kb())

    result = await _meta_call(send, campaigns.update_campaign, campaign_id, name=name)
    if result:
        await message.answer("✅ Назву оновлено.", reply_markup=_menu_kb())


@router.callback_query(F.data.startswith("ads:camp_edit_budget:"))
async def cb_campaign_edit_budget_ask(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    campaign_id = callback.data.split(":")[2]
    await state.update_data(edit_campaign_id=campaign_id)
    await state.set_state(AdsFlow.campaign_edit_budget)
    await callback.message.edit_text("Новий денний бюджет у $ (число, напр. 10):", reply_markup=_cancel_kb())
    await callback.answer()


@router.message(AdsFlow.campaign_edit_budget)
async def fsm_campaign_edit_budget(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    budget = _parse_budget(message.text)
    if budget is None:
        await message.answer("Бюджет має бути додатним числом, напр. 10. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    data = await state.get_data()
    campaign_id = data["edit_campaign_id"]
    await state.clear()

    async def send(text):
        await message.answer(text, reply_markup=_menu_kb())

    result = await _meta_call(send, campaigns.update_campaign, campaign_id, daily_budget_cents=int(round(budget * 100)))
    if result:
        await message.answer("✅ Бюджет оновлено.", reply_markup=_menu_kb())


# --- Створення кампанії (назва → бюджет → ціль) ---

@router.callback_query(F.data == "ads:new_campaign")
async def cb_new_campaign(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    await state.set_state(AdsFlow.campaign_name)
    await callback.message.edit_text("Введіть назву нової кампанії:", reply_markup=_cancel_kb())
    await callback.answer()


@router.message(AdsFlow.campaign_name)
async def fsm_campaign_name(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("Назва не може бути порожньою. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    await state.update_data(campaign_name=name)
    await state.set_state(AdsFlow.campaign_budget)
    await message.answer("Денний бюджет у $ (число, напр. 10):", reply_markup=_cancel_kb())


@router.message(AdsFlow.campaign_budget)
async def fsm_campaign_budget(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    budget = _parse_budget(message.text)
    if budget is None:
        await message.answer("Бюджет має бути додатним числом, напр. 10. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    await state.update_data(campaign_budget=budget)
    await state.set_state(AdsFlow.campaign_objective)
    kb = InlineKeyboardBuilder()
    for obj in OBJECTIVES:
        kb.button(text=OBJECTIVE_LABELS[obj], callback_data=f"ads:obj:{obj}")
    kb.button(text="❌ Скасувати", callback_data="ads:cancel")
    kb.adjust(2, 2, 2, 1)
    await message.answer("Оберіть ціль кампанії:", reply_markup=kb.as_markup())


@router.callback_query(AdsFlow.campaign_objective, F.data.startswith("ads:obj:"))
async def cb_campaign_objective(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    objective = callback.data.split(":")[2]
    data = await state.get_data()
    name = data["campaign_name"]
    budget = data["campaign_budget"]
    await state.clear()
    await callback.answer()

    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    campaign = await _meta_call(
        send, campaigns.create_campaign,
        name=name, objective=objective, daily_budget_cents=int(round(budget * 100)),
    )
    if campaign:
        await callback.message.edit_text(
            f"✅ Кампанія «{name}» створена, id=<code>{campaign['id']}</code>, статус PAUSED.",
            reply_markup=_menu_kb(),
        )


# ---------- Ad set'и ----------

async def _render_adsets(callback: CallbackQuery, campaign_id: str):
    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    rows = await _meta_call(send, adsets.list_ad_sets, campaign_id)
    if rows is None:
        return
    kb = InlineKeyboardBuilder()
    for a in rows:
        kb.button(text=f"{_status_icon(a['status'])} {a['name']}", callback_data=f"ads:adset:{a['id']}")
    kb.button(text="➕ Новий ad set", callback_data=f"ads:new_adset:{campaign_id}")
    kb.button(text="🔙 Назад", callback_data=f"ads:camp:{campaign_id}")
    kb.adjust(1)
    header = "<b>Ad set'и:</b>" if rows else "Ad set'ів ще немає в цій кампанії."
    await callback.message.edit_text(header, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("ads:adsets:"))
async def cb_adsets(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    campaign_id = callback.data.split(":")[2]
    await _render_adsets(callback, campaign_id)
    await callback.answer()


async def _render_adset_detail(callback: CallbackQuery, adset_id: str):
    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    ad_set = await _meta_call(send, adsets.get_ad_set, adset_id)
    if ad_set is None:
        return
    campaign_id = ad_set.get("campaign_id")
    currency = await _get_currency()

    text = (
        f"<b>{ad_set['name']}</b>\n"
        f"Статус: {ad_set['status']}\n"
        f"Бюджет/день: {_fmt_money(ad_set.get('daily_budget'), currency)}"
    )
    kb = InlineKeyboardBuilder()
    if ad_set["status"] == "ACTIVE":
        kb.button(text="⏸️ Пауза", callback_data=f"ads:adset_pause:{adset_id}")
    else:
        kb.button(text="▶️ Запустити", callback_data=f"ads:adset_resume:{adset_id}")
    kb.button(text="📋 Оголошення", callback_data=f"ads:adlist:{adset_id}")
    kb.button(text="➕ Нове оголошення", callback_data=f"ads:new_ad:{adset_id}")
    kb.button(text="📊 Аналітика", callback_data=f"ads:ins:adset:{adset_id}")
    kb.button(text="✏️ Редагувати", callback_data=f"ads:adset_edit:{adset_id}")
    kb.button(text="📄 Дублювати", callback_data=f"ads:adset_dup:{adset_id}")
    kb.button(text="🗑 Видалити", callback_data=f"ads:adset_del_ask:{adset_id}")
    kb.button(text="🔙 Назад", callback_data=f"ads:adsets:{campaign_id}")
    kb.adjust(1)
    await callback.message.edit_text(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("ads:adset:"))
async def cb_adset_detail(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    adset_id = callback.data.split(":")[2]
    await _render_adset_detail(callback, adset_id)
    await callback.answer()


@router.callback_query(F.data.startswith("ads:adset_pause:") | F.data.startswith("ads:adset_resume:"))
async def cb_adset_toggle(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    action, adset_id = callback.data.split(":")[1], callback.data.split(":")[2]
    status = "ACTIVE" if action == "adset_resume" else "PAUSED"

    async def send(text):
        await callback.answer(text, show_alert=True)

    result = await _meta_call(send, adsets.set_ad_set_status, adset_id, status)
    if result is None:
        return
    await callback.answer("✅ Готово")
    await _render_adset_detail(callback, adset_id)


@router.callback_query(F.data.startswith("ads:adset_dup:"))
async def cb_adset_duplicate(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    adset_id = callback.data.split(":")[2]

    async def send(text):
        await callback.answer(text, show_alert=True)

    new_id = await _meta_call(send, adsets.duplicate_ad_set, adset_id)
    if new_id is None:
        return
    await callback.answer("✅ Продубльовано")
    await _render_adset_detail(callback, new_id)


# --- Видалення ad set'а ---

@router.callback_query(F.data.startswith("ads:adset_del_ask:"))
async def cb_adset_delete_ask(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    adset_id = callback.data.split(":")[2]
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Так, видалити", callback_data=f"ads:adset_del:{adset_id}")
    kb.button(text="❌ Ні", callback_data=f"ads:adset:{adset_id}")
    kb.adjust(1)
    await callback.message.edit_text(
        "⚠️ Видалити цей ad set разом з усіма його оголошеннями? Це незворотньо.",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ads:adset_del:"))
async def cb_adset_delete(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    adset_id = callback.data.split(":")[2]

    async def send(text):
        await callback.answer(text, show_alert=True)

    ad_set = await _meta_call(send, adsets.get_ad_set, adset_id)
    if ad_set is None:
        return
    campaign_id = ad_set.get("campaign_id")
    result = await _meta_call(send, adsets.set_ad_set_status, adset_id, "DELETED")
    if result is None:
        return
    await callback.answer("🗑 Видалено")
    await _render_adsets(callback, campaign_id)


# --- Редагування ad set'а ---

@router.callback_query(F.data.startswith("ads:adset_edit:"))
async def cb_adset_edit_menu(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    adset_id = callback.data.split(":")[2]
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Назва", callback_data=f"ads:adset_edit_name:{adset_id}")
    kb.button(text="💰 Бюджет", callback_data=f"ads:adset_edit_budget:{adset_id}")
    kb.button(text="🔙 Назад", callback_data=f"ads:adset:{adset_id}")
    kb.adjust(1)
    await callback.message.edit_text("Що редагувати?", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("ads:adset_edit_name:"))
async def cb_adset_edit_name_ask(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    adset_id = callback.data.split(":")[2]
    await state.update_data(edit_adset_id=adset_id)
    await state.set_state(AdsFlow.adset_edit_name)
    await callback.message.edit_text("Нова назва ad set'а:", reply_markup=_cancel_kb())
    await callback.answer()


@router.message(AdsFlow.adset_edit_name)
async def fsm_adset_edit_name(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("Назва не може бути порожньою. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    data = await state.get_data()
    adset_id = data["edit_adset_id"]
    await state.clear()

    async def send(text):
        await message.answer(text, reply_markup=_menu_kb())

    result = await _meta_call(send, adsets.update_ad_set, adset_id, name=name)
    if result:
        await message.answer("✅ Назву оновлено.", reply_markup=_menu_kb())


@router.callback_query(F.data.startswith("ads:adset_edit_budget:"))
async def cb_adset_edit_budget_ask(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    adset_id = callback.data.split(":")[2]
    await state.update_data(edit_adset_id=adset_id)
    await state.set_state(AdsFlow.adset_edit_budget)
    await callback.message.edit_text("Новий денний бюджет у $ (число, напр. 10):", reply_markup=_cancel_kb())
    await callback.answer()


@router.message(AdsFlow.adset_edit_budget)
async def fsm_adset_edit_budget(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    budget = _parse_budget(message.text)
    if budget is None:
        await message.answer("Бюджет має бути додатним числом, напр. 10. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    data = await state.get_data()
    adset_id = data["edit_adset_id"]
    await state.clear()

    async def send(text):
        await message.answer(text, reply_markup=_menu_kb())

    result = await _meta_call(send, adsets.update_ad_set, adset_id, daily_budget_cents=int(round(budget * 100)))
    if result:
        await message.answer("✅ Бюджет оновлено.", reply_markup=_menu_kb())


# --- Створення ad set'а (назва → бюджет → країни → вік → стать → платформи → інтереси) ---

@router.callback_query(F.data.startswith("ads:new_adset:"))
async def cb_new_adset(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    campaign_id = callback.data.split(":")[2]
    await state.update_data(adset_campaign_id=campaign_id)
    await state.set_state(AdsFlow.adset_name)
    await callback.message.edit_text("Введіть назву ad set'а:", reply_markup=_cancel_kb())
    await callback.answer()


@router.message(AdsFlow.adset_name)
async def fsm_adset_name(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("Назва не може бути порожньою. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    await state.update_data(adset_name=name)
    await state.set_state(AdsFlow.adset_budget)
    await message.answer("Денний бюджет у $ (число, напр. 10):", reply_markup=_cancel_kb())


@router.message(AdsFlow.adset_budget)
async def fsm_adset_budget(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    budget = _parse_budget(message.text)
    if budget is None:
        await message.answer("Бюджет має бути додатним числом, напр. 10. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    await state.update_data(adset_budget=budget)
    await state.set_state(AdsFlow.adset_countries)
    await message.answer("Країни через кому (напр. UA,PL):", reply_markup=_cancel_kb())


@router.message(AdsFlow.adset_countries)
async def fsm_adset_countries(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    countries = [c.strip().upper() for c in (message.text or "").split(",") if c.strip()]
    if not countries:
        await message.answer("Вкажіть хоча б одну країну, напр. UA. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    await state.update_data(adset_countries=countries)
    await state.set_state(AdsFlow.adset_age_min)
    await message.answer("Мінімальний вік аудиторії (13–65, напр. 18):", reply_markup=_cancel_kb())


@router.message(AdsFlow.adset_age_min)
async def fsm_adset_age_min(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    age_min = _parse_age(message.text)
    if age_min is None:
        await message.answer("Вік має бути числом від 13 до 65. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    await state.update_data(adset_age_min=age_min)
    await state.set_state(AdsFlow.adset_age_max)
    await message.answer("Максимальний вік аудиторії (13–65, напр. 65):", reply_markup=_cancel_kb())


@router.message(AdsFlow.adset_age_max)
async def fsm_adset_age_max(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    age_max = _parse_age(message.text)
    data = await state.get_data()
    age_min = data["adset_age_min"]
    if age_max is None or age_max < age_min:
        await message.answer(
            f"Вік має бути числом від {age_min} до 65. Спробуйте ще раз:", reply_markup=_cancel_kb()
        )
        return
    await state.update_data(adset_age_max=age_max)
    await state.set_state(AdsFlow.adset_gender)
    kb = InlineKeyboardBuilder()
    for gender, label in GENDER_LABELS.items():
        kb.button(text=label, callback_data=f"ads:gender:{gender}")
    kb.button(text="❌ Скасувати", callback_data="ads:cancel")
    kb.adjust(3, 1)
    await message.answer("Стать аудиторії:", reply_markup=kb.as_markup())


@router.callback_query(AdsFlow.adset_gender, F.data.startswith("ads:gender:"))
async def cb_adset_gender(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    gender = callback.data.split(":")[2]
    await state.update_data(adset_gender=gender)
    await state.set_state(AdsFlow.adset_platforms)
    kb = InlineKeyboardBuilder()
    for platform, label in PLATFORM_LABELS.items():
        kb.button(text=label, callback_data=f"ads:platform:{platform}")
    kb.button(text="❌ Скасувати", callback_data="ads:cancel")
    kb.adjust(1)
    await callback.message.edit_text("Платформи показу:", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(AdsFlow.adset_platforms, F.data.startswith("ads:platform:"))
async def cb_adset_platforms(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    platform = callback.data.split(":")[2]
    platforms = None if platform == "both" else [platform]
    await state.update_data(adset_platforms=platforms)
    await state.set_state(AdsFlow.adset_interests)
    kb = InlineKeyboardBuilder()
    kb.button(text="🔍 Пошук інтересів", callback_data="ads:int_search")
    kb.button(text="⏭ Без інтересів, створити", callback_data="ads:int_skip")
    kb.button(text="❌ Скасувати", callback_data="ads:cancel")
    kb.adjust(1)
    await callback.message.edit_text(
        "Додати таргетинг за інтересами (напр. фітнес, йога)? Необов'язково.",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


async def _create_ad_set_from_state(callback: CallbackQuery, state: FSMContext, interest_ids: list[str] | None):
    data = await state.get_data()
    campaign_id = data["adset_campaign_id"]
    name = data["adset_name"]
    budget = data["adset_budget"]
    countries = data["adset_countries"]
    age_min = data["adset_age_min"]
    age_max = data["adset_age_max"]
    gender = data["adset_gender"]
    platforms = data["adset_platforms"]
    await state.clear()

    spec = targeting.build_targeting(
        countries=countries, age_min=age_min, age_max=age_max, gender=gender,
        interest_ids=interest_ids or None, platforms=platforms,
    )

    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    ad_set = await _meta_call(
        send, adsets.create_ad_set, campaign_id=campaign_id, name=name,
        daily_budget_cents=int(round(budget * 100)), targeting=spec,
    )
    if ad_set:
        await callback.message.edit_text(
            f"✅ Ad set «{name}» створено, id=<code>{ad_set['id']}</code>, статус PAUSED.",
            reply_markup=_menu_kb(),
        )


@router.callback_query(AdsFlow.adset_interests, F.data == "ads:int_skip")
async def cb_interests_skip(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    await callback.answer()
    await callback.message.edit_text("Створюю ad set...")
    await _create_ad_set_from_state(callback, state, interest_ids=None)


@router.callback_query(AdsFlow.adset_interests, F.data == "ads:int_search")
async def cb_interests_search_ask(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    await state.set_state(AdsFlow.adset_interest_query)
    await callback.message.edit_text(
        "Ключове слово для пошуку інтересів (напр. фітнес):", reply_markup=_cancel_kb()
    )
    await callback.answer()


def _interest_pick_kb(found: dict, selected: set):
    kb = InlineKeyboardBuilder()
    for interest_id, name in found.items():
        mark = "☑️" if interest_id in selected else "☐"
        kb.button(text=f"{mark} {name}", callback_data=f"ads:int_toggle:{interest_id}")
    kb.button(text=f"✅ Готово ({len(selected)})", callback_data="ads:int_done")
    kb.button(text="🔍 Новий пошук", callback_data="ads:int_search")
    kb.button(text="❌ Скасувати", callback_data="ads:cancel")
    kb.adjust(1)
    return kb.as_markup()


@router.message(AdsFlow.adset_interest_query)
async def fsm_interest_query(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введіть ключове слово. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return

    async def send(text):
        await message.answer(text, reply_markup=_cancel_kb())

    results = await _meta_call(send, targeting.search_interests, query, 8)
    if results is None:
        return
    if not results:
        kb = InlineKeyboardBuilder()
        kb.button(text="🔍 Новий пошук", callback_data="ads:int_search")
        kb.button(text="⏭ Без інтересів, створити", callback_data="ads:int_skip")
        kb.button(text="❌ Скасувати", callback_data="ads:cancel")
        kb.adjust(1)
        await state.set_state(AdsFlow.adset_interests)
        await message.answer("Нічого не знайдено. Спробуйте інше слово:", reply_markup=kb.as_markup())
        return

    data = await state.get_data()
    found = dict(data.get("interest_found") or {})
    for r in results:
        found[r["id"]] = r["name"]
    selected = set(data.get("interest_selected") or [])
    await state.update_data(interest_found=found, interest_selected=list(selected))
    await state.set_state(AdsFlow.adset_interest_pick)
    await message.answer("Оберіть інтереси (тап — увімкнути/вимкнути):", reply_markup=_interest_pick_kb(found, selected))


@router.callback_query(AdsFlow.adset_interest_pick, F.data.startswith("ads:int_toggle:"))
async def cb_interest_toggle(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    interest_id = callback.data.split(":")[2]
    data = await state.get_data()
    found = dict(data.get("interest_found") or {})
    selected = set(data.get("interest_selected") or [])
    if interest_id in selected:
        selected.discard(interest_id)
    else:
        selected.add(interest_id)
    await state.update_data(interest_selected=list(selected))
    await callback.message.edit_text(
        "Оберіть інтереси (тап — увімкнути/вимкнути):", reply_markup=_interest_pick_kb(found, selected)
    )
    await callback.answer()


@router.callback_query(AdsFlow.adset_interest_pick, F.data == "ads:int_search")
async def cb_interest_search_again(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    await state.set_state(AdsFlow.adset_interest_query)
    await callback.message.edit_text("Ключове слово для пошуку інтересів:", reply_markup=_cancel_kb())
    await callback.answer()


@router.callback_query(AdsFlow.adset_interest_pick, F.data == "ads:int_done")
async def cb_interest_done(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    data = await state.get_data()
    selected = list(data.get("interest_selected") or [])
    await callback.answer()
    await callback.message.edit_text("Створюю ad set...")
    await _create_ad_set_from_state(callback, state, interest_ids=selected)


# ---------- Оголошення (список і керування) ----------

async def _render_ad_list(callback: CallbackQuery, adset_id: str):
    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    rows = await _meta_call(send, ads.list_ads, adset_id)
    if rows is None:
        return
    kb = InlineKeyboardBuilder()
    for a in rows:
        kb.button(text=f"{_status_icon(a['status'])} {a['name']}", callback_data=f"ads:ad:{a['id']}")
    kb.button(text="➕ Нове оголошення", callback_data=f"ads:new_ad:{adset_id}")
    kb.button(text="🔙 Назад", callback_data=f"ads:adset:{adset_id}")
    kb.adjust(1)
    header = "<b>Оголошення:</b>" if rows else "Оголошень ще немає в цьому ad set'і."
    await callback.message.edit_text(header, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("ads:adlist:"))
async def cb_ad_list(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    adset_id = callback.data.split(":")[2]
    await _render_ad_list(callback, adset_id)
    await callback.answer()


async def _render_ad_detail(callback: CallbackQuery, ad_id: str):
    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    ad = await _meta_call(send, ads.get_ad, ad_id)
    if ad is None:
        return
    adset_id = ad.get("adset_id")

    text = f"<b>{ad['name']}</b>\nСтатус: {ad['status']}"
    kb = InlineKeyboardBuilder()
    if ad["status"] == "ACTIVE":
        kb.button(text="⏸️ Пауза", callback_data=f"ads:ad_pause:{ad_id}")
    else:
        kb.button(text="▶️ Запустити", callback_data=f"ads:ad_resume:{ad_id}")
    kb.button(text="📊 Аналітика", callback_data=f"ads:ins:ad:{ad_id}")
    kb.button(text="✏️ Назва", callback_data=f"ads:ad_edit_name:{ad_id}")
    kb.button(text="🗑 Видалити", callback_data=f"ads:ad_del_ask:{ad_id}")
    kb.button(text="🔙 Назад", callback_data=f"ads:adlist:{adset_id}")
    kb.adjust(1)
    await callback.message.edit_text(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("ads:ad:"))
async def cb_ad_detail(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    ad_id = callback.data.split(":")[2]
    await _render_ad_detail(callback, ad_id)
    await callback.answer()


@router.callback_query(F.data.startswith("ads:ad_pause:") | F.data.startswith("ads:ad_resume:"))
async def cb_ad_toggle(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    action, ad_id = callback.data.split(":")[1], callback.data.split(":")[2]
    status = "ACTIVE" if action == "ad_resume" else "PAUSED"

    async def send(text):
        await callback.answer(text, show_alert=True)

    result = await _meta_call(send, ads.set_ad_status, ad_id, status)
    if result is None:
        return
    await callback.answer("✅ Готово")
    await _render_ad_detail(callback, ad_id)


@router.callback_query(F.data.startswith("ads:ad_del_ask:"))
async def cb_ad_delete_ask(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    ad_id = callback.data.split(":")[2]
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Так, видалити", callback_data=f"ads:ad_del:{ad_id}")
    kb.button(text="❌ Ні", callback_data=f"ads:ad:{ad_id}")
    kb.adjust(1)
    await callback.message.edit_text("⚠️ Видалити це оголошення? Це незворотньо.", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("ads:ad_del:"))
async def cb_ad_delete(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    ad_id = callback.data.split(":")[2]

    async def send(text):
        await callback.answer(text, show_alert=True)

    ad = await _meta_call(send, ads.get_ad, ad_id)
    if ad is None:
        return
    adset_id = ad.get("adset_id")
    result = await _meta_call(send, ads.set_ad_status, ad_id, "DELETED")
    if result is None:
        return
    await callback.answer("🗑 Видалено")
    await _render_ad_list(callback, adset_id)


@router.callback_query(F.data.startswith("ads:ad_edit_name:"))
async def cb_ad_edit_name_ask(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    ad_id = callback.data.split(":")[2]
    await state.update_data(edit_ad_id=ad_id)
    await state.set_state(AdsFlow.ad_edit_name)
    await callback.message.edit_text("Нова назва оголошення:", reply_markup=_cancel_kb())
    await callback.answer()


@router.message(AdsFlow.ad_edit_name)
async def fsm_ad_edit_name(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("Назва не може бути порожньою. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    data = await state.get_data()
    ad_id = data["edit_ad_id"]
    await state.clear()

    async def send(text):
        await message.answer(text, reply_markup=_menu_kb())

    result = await _meta_call(send, ads.update_ad, ad_id, name)
    if result:
        await message.answer("✅ Назву оновлено.", reply_markup=_menu_kb())


# ---------- Створення оголошення (креатив) ----------

@router.callback_query(F.data.startswith("ads:new_ad:"))
async def cb_new_ad(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    adset_id = callback.data.split(":")[2]
    await state.update_data(ad_adset_id=adset_id)
    await state.set_state(AdsFlow.ad_link)
    await callback.message.edit_text("Посилання, на яке веде оголошення:", reply_markup=_cancel_kb())
    await callback.answer()


@router.message(AdsFlow.ad_link)
async def fsm_ad_link(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    link = (message.text or "").strip()
    if not link.startswith("http"):
        await message.answer("Схоже, це не посилання. Спробуйте ще раз (напр. https://t.me/...):", reply_markup=_cancel_kb())
        return
    await state.update_data(ad_link=link)
    await state.set_state(AdsFlow.ad_message)
    await message.answer("Текст оголошення:", reply_markup=_cancel_kb())


@router.message(AdsFlow.ad_message)
async def fsm_ad_message(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Текст не може бути порожнім. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    await state.update_data(ad_message=text)
    await state.set_state(AdsFlow.ad_headline)
    await message.answer("Заголовок оголошення:", reply_markup=_cancel_kb())


@router.message(AdsFlow.ad_headline)
async def fsm_ad_headline(message: Message, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    headline = (message.text or "").strip()
    if not headline:
        await message.answer("Заголовок не може бути порожнім. Спробуйте ще раз:", reply_markup=_cancel_kb())
        return
    await state.update_data(ad_headline=headline)
    await state.set_state(AdsFlow.ad_photo)
    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Пропустити", callback_data="ads:skip_photo")
    kb.button(text="❌ Скасувати", callback_data="ads:cancel")
    kb.adjust(1)
    await message.answer("Надішліть фото для оголошення, або натисніть «Пропустити»:", reply_markup=kb.as_markup())


async def _finish_ad_creation(responder, bot: Bot, state: FSMContext, photo_file_id: str | None):
    data = await state.get_data()
    adset_id = data["ad_adset_id"]
    link = data["ad_link"]
    text = data["ad_message"]
    headline = data["ad_headline"]
    await state.clear()

    image_hash = None
    if photo_file_id:
        try:
            load_meta_config()
        except RuntimeError as e:
            await responder(f"⚠️ {e}")
            return
        file = await bot.get_file(photo_file_id)
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            await bot.download_file(file.file_path, tmp.name)
            image_hash = await _meta_call(responder, ads.upload_image, tmp.name)
            if image_hash is None:
                return

    creative = await _meta_call(
        responder, ads.create_link_creative, link=link, message=text, headline=headline, image_hash=image_hash,
    )
    if not creative:
        return
    ad = await _meta_call(responder, ads.create_ad, ad_set_id=adset_id, name=headline, creative_id=creative["id"])
    if ad:
        await responder(f"✅ Оголошення «{headline}» створено, id=<code>{ad['id']}</code>, статус PAUSED.")


@router.callback_query(AdsFlow.ad_photo, F.data == "ads:skip_photo")
async def cb_skip_photo(callback: CallbackQuery, state: FSMContext, bot: Bot, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    await callback.answer()
    await callback.message.edit_text("Створюю оголошення...")

    async def responder(text):
        await callback.message.answer(text, reply_markup=_menu_kb())

    await _finish_ad_creation(responder, bot, state, photo_file_id=None)


@router.message(AdsFlow.ad_photo, F.photo)
async def fsm_ad_photo(message: Message, state: FSMContext, bot: Bot, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    await message.answer("Створюю оголошення...")

    async def responder(text):
        await message.answer(text, reply_markup=_menu_kb())

    await _finish_ad_creation(responder, bot, state, photo_file_id=message.photo[-1].file_id)


@router.message(AdsFlow.ad_photo)
async def fsm_ad_photo_invalid(message: Message, admin_ids: list[int]):
    if not _is_admin(message.from_user, admin_ids):
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Пропустити", callback_data="ads:skip_photo")
    kb.button(text="❌ Скасувати", callback_data="ads:cancel")
    kb.adjust(1)
    await message.answer("Надішліть саме фото, або натисніть «Пропустити»:", reply_markup=kb.as_markup())


# ---------- Аналітика (кампанія / ad set / оголошення) ----------

def _back_target(level: str, object_id: str) -> str:
    prefix = {"campaign": "ads:camp", "adset": "ads:adset", "ad": "ads:ad"}[level]
    return f"{prefix}:{object_id}"


@router.callback_query(F.data.startswith("ads:ins:"))
async def cb_insights_menu(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    _, _, level, object_id = callback.data.split(":")
    kb = InlineKeyboardBuilder()
    for preset in DATE_PRESETS:
        kb.button(text=DATE_PRESET_LABELS[preset], callback_data=f"ads:ins_show:{level}:{object_id}:{preset}")
    kb.button(text="🔙 Назад", callback_data=_back_target(level, object_id))
    kb.adjust(2, 2, 1)
    await callback.message.edit_text("За який період?", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("ads:ins_show:"))
async def cb_insights_show(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    _, _, level, object_id, date_preset = callback.data.split(":")

    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    rows = await _meta_call(send, insights.get_insights, object_id, level, date_preset)
    await callback.answer()
    if rows is None:
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Назад", callback_data=_back_target(level, object_id))
    if not rows:
        await callback.message.edit_text("Даних поки немає.", reply_markup=kb.as_markup())
        return
    table = tabulate(rows, headers="keys")
    await callback.message.edit_text(f"<pre>{table}</pre>", reply_markup=kb.as_markup())
