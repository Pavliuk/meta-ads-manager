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

Усе, що створюється цим інструментом, за замовчуванням має статус **PAUSED** —
показ не почнеться, доки ви свідомо не активуєте кампанію (`--active` або
`campaign resume`). Це навмисно, щоб випадковий запуск скрипта не почав
витрачати бюджет.

## Технічний стек

- Python 3.10+
- [`facebook-business`](https://github.com/facebook/facebook-python-business-sdk) — офіційний SDK Meta Marketing API
- [Typer](https://typer.tiangolo.com/) — CLI

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

### Трекінг конверсії з реклами

Параметр `?start=fb_ads` / `?start=ig_ads` у посиланні на Telegram-бота дозволяє
на боці бота відрізнити користувачів, що прийшли з реклами, від органічних —
якщо бот, на який ведете трафік, уже вміє читати цей параметр із deep-link'а
`/start`, у `/traffic`-подібній команді ви побачите реальну конверсію
реклама → реєстрація.

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
```

## Застереження

- Реальні гроші: кожна активована кампанія витрачає бюджет одразу. Завжди перевіряйте
  ad set і оголошення в Ads Manager (https://www.facebook.com/adsmanager) перед `resume`.
- Дотримуйтесь [рекламної політики Meta](https://www.facebook.com/policies/ads/) —
  акаунт можуть заблокувати за порушення (заборонений контент, оманливі обіцянки тощо).
- `.env` з токеном ніколи не комітьте — він уже в `.gitignore`.
