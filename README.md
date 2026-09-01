# 📣 Meta Ads Manager

CLI для керування таргетованою рекламою у **Facebook та Instagram** через офіційний
[Meta Marketing API](https://developers.facebook.com/docs/marketing-apis/) — створення
кампаній, груп оголошень (ad sets), креативів та оголошень, таргетинг, бюджети,
аналітика. Незалежний проєкт, не пов'язаний з іншими репозиторіями.

## Функціонал

- **Кампанії**: створення (ціль — трафік/ліди/охоплення/конверсії/…), список, пауза/запуск
- **Ad sets**: бюджет, розклад, таргетинг (країни, вік, стать, інтереси, платформи FB/IG)
- **Креативи та оголошення**: посилання + текст + заголовок + картинка → оголошення
- **Пошук інтересів**: підбір ID інтересів для таргетингу за назвою
- **Аналітика**: витрати, покази, кліки, CTR, CPC по кампанії/ad set'у/оголошенню
- **Лендинг-бот** (`landing_bot/`): окремий мінімальний Telegram-бот, на який веде
  посилання з реклами — фіксує джерело трафіку (`?start=fb_ads` / `?start=ig_ads`)
  і показує конверсію через `/traffic`. Замикає весь цикл перевірки в межах цього
  ж проєкту, без залежності від сторонніх ботів.

Усе, що створюється цим інструментом, за замовчуванням має статус **PAUSED** —
показ не почнеться, доки ви свідомо не активуєте кампанію (`--active` або
`campaign resume`). Це навмисно, щоб випадковий запуск скрипта не почав
витрачати бюджет.

## Технічний стек

- Python 3.10+
- [`facebook-business`](https://github.com/facebook/facebook-python-business-sdk) — офіційний SDK Meta Marketing API
- [Typer](https://typer.tiangolo.com/) — CLI
- [aiogram 3](https://docs.aiogram.dev/) + SQLite — лендинг-бот

## Встановлення

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Отримання доступу до Meta Marketing API

У вас ще немає жодного налаштування — виконайте по черзі:

### 1. Business Manager

Створіть Business Manager на https://business.facebook.com/, якщо його ще немає.
До нього потрібно прив'язати:
- **Facebook-сторінку**, від імені якої йтимуть оголошення (Business Settings → Accounts → Pages)
- **Рекламний акаунт** (Business Settings → Accounts → Ad Accounts) — прив'яжіть існуючий
  або створіть новий, додайте спосіб оплати

### 2. Застосунок на developers.facebook.com

1. Зайдіть на https://developers.facebook.com/apps/ → **Create App**
2. Тип застосунку — **Business**
3. Прив'яжіть застосунок до вашого Business Manager (App settings → Basic → Business Account)
4. Додайте продукт **Marketing API** (App Dashboard → Add Product)
5. Скопіюйте **App ID** та **App Secret** (App settings → Basic) → `META_APP_ID`, `META_APP_SECRET` у `.env`

### 3. Access token — System User (рекомендовано)

Звичайний токен, отриманий через Graph API Explorer, прив'язаний до вашого особистого
логіну і швидко протухає. Для скрипта, що працює без нагляду, зручніший **System User**:

1. Business Settings → Users → System Users → **Add** → створіть System User з роллю Admin
2. **Add Assets** → додайте ваш рекламний акаунт з правом **Manage campaigns**
3. **Generate New Token** → оберіть ваш застосунок → права (permissions):
   `ads_management`, `ads_read`, `business_management`, `pages_read_engagement`
4. Скопійований токен → `META_ACCESS_TOKEN` у `.env`

Токен System User не має терміну дії (доки ви його не відкличете) — не потребує
регулярного оновлення.

### 4. App Review

Поки застосунок у режимі розробки (Development), токени System User з ролями Admin/Employee
самого Business Manager вже можуть повноцінно керувати **вашими власними** рекламними
акаунтами — App Review для цього **не обов'язковий**. App Review потрібен лише якщо:
- хочете, щоб застосунком користувались люди поза вашим Business Manager, або
- потрібні розширені permissions (напр. доступ до чужих сторінок).

Тобто для персонального використання (керувати своєю ж рекламою) можна почати
одразу після кроків 1–3.

### 5. ID рекламного акаунту та сторінки

- `META_AD_ACCOUNT_ID` — Business Settings → Accounts → Ad Accounts → ID акаунту
  (з префіксом `act_` або без — скрипт додасть сам)
- `META_PAGE_ID` — Business Settings → Accounts → Pages → ID сторінки

## Використання

```bash
# Кампанія з ціллю "трафік на посилання" (бот/лендінг), $10/день, PAUSED
python -m meta_ads campaign create --name "Промо бота" --daily-budget 10

python -m meta_ads campaign list

# Пошук інтересів для таргетингу
python -m meta_ads interests search "фітнес"

# Ad set: Україна, 18-45, обидва пола, за інтересом "фітнес" (id з пошуку вище)
python -m meta_ads adset create <campaign_id> "UA 18-45" \
    --daily-budget 10 --countries UA --age-min 18 --age-max 45 \
    --interests 6003107902433

# Креатив: посилання на бота з міткою джерела трафіку (?start=fb_ads)
python -m meta_ads ad create-creative \
    --link "https://t.me/my_fitness_bot?start=fb_ads" \
    --message "Персональний план тренувань і харчування за 2 хвилини" \
    --headline "Спробувати безкоштовно" \
    --page-id <page_id>

# Оголошення в ad set на основі креативу
python -m meta_ads ad create <ad_set_id> "Оголошення 1" <creative_id>

# Коли перевірили в Ads Manager, що все виглядає правильно — вмикаєте показ:
python -m meta_ads campaign resume <campaign_id>
python -m meta_ads adset resume <ad_set_id>
python -m meta_ads ad resume <ad_id>

# Аналітика
python -m meta_ads insights show --id <campaign_id> --level campaign --date-preset last_7d
```

### Лендинг-бот: трекінг конверсії з реклами

Посилання, на яке веде реклама, — це власний Telegram-бот цього проєкту
(`landing_bot/`), а не сторонній сервіс. Він приймає параметр `?start=fb_ads` /
`?start=ig_ads` з диплінку і фіксує джерело кожного нового користувача.

**Встановлення бота:**
1. [@BotFather](https://t.me/BotFather) → `/newbot` → отримайте токен
2. У `.env`: `LANDING_BOT_TOKEN=<токен>`, `ADMIN_IDS=<ваш tg_id>` (дізнатись tg_id —
   написати [@userinfobot](https://t.me/userinfobot))

**Запуск:**
```bash
python -m landing_bot
```
Піднімається через polling і чекає на повідомлення (термінал "зависає" — це нормально).

**Перевірка:**
1. Відкрийте `https://t.me/<username_бота>?start=fb_ads`, натисніть Start
2. Бот відповість, що бачить джерело `fb_ads`
3. Напишіть боту `/traffic` (з акаунту, чий tg_id у `ADMIN_IDS`) — побачите таблицю
   розподілу користувачів за джерелом

Саме на цей бот і мають вести посилання, які ви вказуєте в `ad create-creative --link`
при створенні реклами через CLI вище.

## Структура проєкту

```
meta_ads/
├── config.py      # завантаження .env
├── client.py      # ініціалізація FacebookAdsApi, AdAccount
├── campaigns.py   # Campaign: create/list/pause/resume
├── adsets.py      # AdSet: create/list/pause/resume
├── ads.py         # AdCreative, Ad: create/pause/resume
├── targeting.py   # побудова targeting spec, пошук інтересів
├── insights.py    # аналітика (Insights API)
└── cli.py         # Typer CLI

landing_bot/
├── config.py      # LANDING_BOT_TOKEN, ADMIN_IDS
├── db.py          # SQLite: users + acquisition_source
└── bot.py         # /start (фіксує джерело), /traffic (статистика)
```

## Застереження

- Реальні гроші: кожна активована кампанія витрачає бюджет одразу. Завжди перевіряйте
  ad set і оголошення в Ads Manager (https://www.facebook.com/adsmanager) перед `resume`.
- Дотримуйтесь [рекламної політики Meta](https://www.facebook.com/policies/ads/) —
  акаунт можуть заблокувати за порушення (заборонений контент, оманливі обіцянки тощо).
- `.env` з токеном ніколи не комітьте — він уже в `.gitignore`.
