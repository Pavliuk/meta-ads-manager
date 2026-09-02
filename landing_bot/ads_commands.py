"""Кнопкове (inline-keyboard) керування рекламою Facebook/Instagram з Telegram.

Той самий Meta Marketing API, що й `python -m meta_ads`, тільки через меню
замість команд із синтаксисом. Створення/редагування/видалення кампанії,
ad set'а чи оголошення — короткий покроковий діалог (FSM): бот питає одне
поле за раз, з кнопкою «Скасувати» на кожному кроці; видалення — з окремим
підтвердженням.

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

from meta_ads import adsets, ads, campaigns, insights, targeting
from meta_ads.campaigns import OBJECTIVES
from meta_ads.config import load_config as load_meta_config

router = Router(name="ads_commands")
logger = logging.getLogger(__name__)

DATE_PRESETS = ("today", "last_7d", "last_30d", "last_90d")

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
    adset_edit_name = State()
    adset_edit_budget = State()

    ad_link = State()
    ad_message = State()
    ad_headline = State()
    ad_photo = State()


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
            "Meta API error: type=%s code=%s subcode=%s message=%s",
            e.api_error_type(), e.api_error_code(), e.api_error_subcode(), e.api_error_message(),
        )
        await send(
            f"❌ Meta API [{e.api_error_code()}/{e.api_error_subcode()}]: {e.api_error_message()}"
        )
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

    rows = await _meta_call(send, campaigns.list_campaigns)
    if rows is None:
        return
    campaign = next((c for c in rows if c["id"] == campaign_id), None)
    if campaign is None:
        await callback.message.edit_text("Кампанію не знайдено (можливо, видалена).", reply_markup=_menu_kb())
        return

    text = (
        f"<b>{campaign['name']}</b>\n"
        f"Статус: {campaign['status']}\n"
        f"Ціль: {campaign.get('objective') or '—'}\n"
        f"Бюджет/день: {campaign.get('daily_budget') or '—'}"
    )
    kb = InlineKeyboardBuilder()
    if campaign["status"] == "ACTIVE":
        kb.button(text="⏸️ Пауза", callback_data=f"ads:camp_pause:{campaign_id}")
    else:
        kb.button(text="▶️ Запустити", callback_data=f"ads:camp_resume:{campaign_id}")
    kb.button(text="🎯 Ad sets", callback_data=f"ads:adsets:{campaign_id}")
    kb.button(text="📊 Аналітика", callback_data=f"ads:insights:{campaign_id}")
    kb.button(text="✏️ Редагувати", callback_data=f"ads:camp_edit:{campaign_id}")
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
        kb.button(text=f"{_status_icon(a['status'])} {a['name']}", callback_data=f"ads:adset:{a['id']}:{campaign_id}")
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


async def _render_adset_detail(callback: CallbackQuery, adset_id: str, campaign_id: str):
    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    rows = await _meta_call(send, adsets.list_ad_sets, campaign_id)
    if rows is None:
        return
    ad_set = next((a for a in rows if a["id"] == adset_id), None)
    if ad_set is None:
        await callback.message.edit_text("Ad set не знайдено.", reply_markup=_menu_kb())
        return

    text = (
        f"<b>{ad_set['name']}</b>\n"
        f"Статус: {ad_set['status']}\n"
        f"Бюджет/день: {ad_set.get('daily_budget') or '—'}"
    )
    kb = InlineKeyboardBuilder()
    if ad_set["status"] == "ACTIVE":
        kb.button(text="⏸️ Пауза", callback_data=f"ads:adset_pause:{adset_id}:{campaign_id}")
    else:
        kb.button(text="▶️ Запустити", callback_data=f"ads:adset_resume:{adset_id}:{campaign_id}")
    kb.button(text="📋 Оголошення", callback_data=f"ads:adlist:{adset_id}:{campaign_id}")
    kb.button(text="➕ Нове оголошення", callback_data=f"ads:new_ad:{adset_id}:{campaign_id}")
    kb.button(text="✏️ Редагувати", callback_data=f"ads:adset_edit:{adset_id}:{campaign_id}")
    kb.button(text="🗑 Видалити", callback_data=f"ads:adset_del_ask:{adset_id}:{campaign_id}")
    kb.button(text="🔙 Назад", callback_data=f"ads:adsets:{campaign_id}")
    kb.adjust(1)
    await callback.message.edit_text(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("ads:adset:"))
async def cb_adset_detail(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    _, _, adset_id, campaign_id = callback.data.split(":")
    await _render_adset_detail(callback, adset_id, campaign_id)
    await callback.answer()


@router.callback_query(F.data.startswith("ads:adset_pause:") | F.data.startswith("ads:adset_resume:"))
async def cb_adset_toggle(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    action, adset_id, campaign_id = callback.data.split(":")[1:4]
    status = "ACTIVE" if action == "adset_resume" else "PAUSED"

    async def send(text):
        await callback.answer(text, show_alert=True)

    result = await _meta_call(send, adsets.set_ad_set_status, adset_id, status)
    if result is None:
        return
    await callback.answer("✅ Готово")
    await _render_adset_detail(callback, adset_id, campaign_id)


# --- Видалення ad set'а ---

@router.callback_query(F.data.startswith("ads:adset_del_ask:"))
async def cb_adset_delete_ask(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    _, _, adset_id, campaign_id = callback.data.split(":")
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Так, видалити", callback_data=f"ads:adset_del:{adset_id}:{campaign_id}")
    kb.button(text="❌ Ні", callback_data=f"ads:adset:{adset_id}:{campaign_id}")
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
    _, _, adset_id, campaign_id = callback.data.split(":")

    async def send(text):
        await callback.answer(text, show_alert=True)

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
    _, _, adset_id, campaign_id = callback.data.split(":")
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Назва", callback_data=f"ads:adset_edit_name:{adset_id}:{campaign_id}")
    kb.button(text="💰 Бюджет", callback_data=f"ads:adset_edit_budget:{adset_id}:{campaign_id}")
    kb.button(text="🔙 Назад", callback_data=f"ads:adset:{adset_id}:{campaign_id}")
    kb.adjust(1)
    await callback.message.edit_text("Що редагувати?", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("ads:adset_edit_name:"))
async def cb_adset_edit_name_ask(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    _, _, adset_id, campaign_id = callback.data.split(":")
    await state.update_data(edit_adset_id=adset_id, edit_adset_campaign_id=campaign_id)
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
    _, _, adset_id, campaign_id = callback.data.split(":")
    await state.update_data(edit_adset_id=adset_id, edit_adset_campaign_id=campaign_id)
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


# --- Створення ad set'а (назва → бюджет → країни → вік → стать → платформи) ---

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

    data = await state.get_data()
    campaign_id = data["adset_campaign_id"]
    name = data["adset_name"]
    budget = data["adset_budget"]
    countries = data["adset_countries"]
    age_min = data["adset_age_min"]
    age_max = data["adset_age_max"]
    gender = data["adset_gender"]
    await state.clear()
    await callback.answer()

    spec = targeting.build_targeting(
        countries=countries, age_min=age_min, age_max=age_max, gender=gender, platforms=platforms,
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


# ---------- Оголошення (список і керування) ----------

async def _render_ad_list(callback: CallbackQuery, adset_id: str, campaign_id: str):
    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    rows = await _meta_call(send, ads.list_ads, adset_id)
    if rows is None:
        return
    kb = InlineKeyboardBuilder()
    for a in rows:
        kb.button(
            text=f"{_status_icon(a['status'])} {a['name']}",
            callback_data=f"ads:ad:{a['id']}:{adset_id}:{campaign_id}",
        )
    kb.button(text="➕ Нове оголошення", callback_data=f"ads:new_ad:{adset_id}:{campaign_id}")
    kb.button(text="🔙 Назад", callback_data=f"ads:adset:{adset_id}:{campaign_id}")
    kb.adjust(1)
    header = "<b>Оголошення:</b>" if rows else "Оголошень ще немає в цьому ad set'і."
    await callback.message.edit_text(header, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("ads:adlist:"))
async def cb_ad_list(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    _, _, adset_id, campaign_id = callback.data.split(":")
    await _render_ad_list(callback, adset_id, campaign_id)
    await callback.answer()


async def _render_ad_detail(callback: CallbackQuery, ad_id: str, adset_id: str, campaign_id: str):
    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    rows = await _meta_call(send, ads.list_ads, adset_id)
    if rows is None:
        return
    ad = next((a for a in rows if a["id"] == ad_id), None)
    if ad is None:
        await callback.message.edit_text("Оголошення не знайдено.", reply_markup=_menu_kb())
        return

    text = f"<b>{ad['name']}</b>\nСтатус: {ad['status']}"
    kb = InlineKeyboardBuilder()
    if ad["status"] == "ACTIVE":
        kb.button(text="⏸️ Пауза", callback_data=f"ads:ad_pause:{ad_id}:{adset_id}:{campaign_id}")
    else:
        kb.button(text="▶️ Запустити", callback_data=f"ads:ad_resume:{ad_id}:{adset_id}:{campaign_id}")
    kb.button(text="🗑 Видалити", callback_data=f"ads:ad_del_ask:{ad_id}:{adset_id}:{campaign_id}")
    kb.button(text="🔙 Назад", callback_data=f"ads:adlist:{adset_id}:{campaign_id}")
    kb.adjust(1)
    await callback.message.edit_text(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("ads:ad:"))
async def cb_ad_detail(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    _, _, ad_id, adset_id, campaign_id = callback.data.split(":")
    await _render_ad_detail(callback, ad_id, adset_id, campaign_id)
    await callback.answer()


@router.callback_query(F.data.startswith("ads:ad_pause:") | F.data.startswith("ads:ad_resume:"))
async def cb_ad_toggle(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    action, ad_id, adset_id, campaign_id = callback.data.split(":")[1:5]
    status = "ACTIVE" if action == "ad_resume" else "PAUSED"

    async def send(text):
        await callback.answer(text, show_alert=True)

    result = await _meta_call(send, ads.set_ad_status, ad_id, status)
    if result is None:
        return
    await callback.answer("✅ Готово")
    await _render_ad_detail(callback, ad_id, adset_id, campaign_id)


@router.callback_query(F.data.startswith("ads:ad_del_ask:"))
async def cb_ad_delete_ask(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    _, _, ad_id, adset_id, campaign_id = callback.data.split(":")
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Так, видалити", callback_data=f"ads:ad_del:{ad_id}:{adset_id}:{campaign_id}")
    kb.button(text="❌ Ні", callback_data=f"ads:ad:{ad_id}:{adset_id}:{campaign_id}")
    kb.adjust(1)
    await callback.message.edit_text("⚠️ Видалити це оголошення? Це незворотньо.", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("ads:ad_del:"))
async def cb_ad_delete(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    _, _, ad_id, adset_id, campaign_id = callback.data.split(":")

    async def send(text):
        await callback.answer(text, show_alert=True)

    result = await _meta_call(send, ads.set_ad_status, ad_id, "DELETED")
    if result is None:
        return
    await callback.answer("🗑 Видалено")
    await _render_ad_list(callback, adset_id, campaign_id)


# ---------- Створення оголошення (креатив) ----------

@router.callback_query(F.data.startswith("ads:new_ad:"))
async def cb_new_ad(callback: CallbackQuery, state: FSMContext, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    _, _, adset_id, campaign_id = callback.data.split(":")
    await state.update_data(ad_adset_id=adset_id, ad_campaign_id=campaign_id)
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


# ---------- Аналітика ----------

@router.callback_query(F.data.startswith("ads:insights:"))
async def cb_insights_menu(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    campaign_id = callback.data.split(":")[2]
    kb = InlineKeyboardBuilder()
    labels = {"today": "Сьогодні", "last_7d": "7 днів", "last_30d": "30 днів", "last_90d": "90 днів"}
    for preset in DATE_PRESETS:
        kb.button(text=labels[preset], callback_data=f"ads:insights_show:{campaign_id}:{preset}")
    kb.button(text="🔙 Назад", callback_data=f"ads:camp:{campaign_id}")
    kb.adjust(2, 2, 1)
    await callback.message.edit_text("За який період?", reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("ads:insights_show:"))
async def cb_insights_show(callback: CallbackQuery, admin_ids: list[int]):
    if not _is_admin(callback.from_user, admin_ids):
        return await callback.answer()
    _, _, campaign_id, date_preset = callback.data.split(":")

    async def send(text):
        await callback.message.edit_text(text, reply_markup=_menu_kb())

    rows = await _meta_call(send, insights.get_insights, campaign_id, "campaign", date_preset)
    await callback.answer()
    if rows is None:
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Назад", callback_data=f"ads:camp:{campaign_id}")
    if not rows:
        await callback.message.edit_text("Даних поки немає.", reply_markup=kb.as_markup())
        return
    table = tabulate(rows, headers="keys")
    await callback.message.edit_text(f"<pre>{table}</pre>", reply_markup=kb.as_markup())
