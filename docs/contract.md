# Контракт «Сургутский плейс» (MVP)

Корень проекта: `~/surgut-place`. Python ≥3.11 (локально 3.14), `uv`, `ruff`, `pytest`.
Стек: aiogram 3.x (long polling), SQLAlchemy 2.x async, Alembic, APScheduler 3.x, python-dotenv.
БД: прод — PostgreSQL (`postgresql+asyncpg://`), dev/тесты — SQLite (`sqlite+aiosqlite://`). Модели должны работать на обеих
(никаких `ARRAY`/PG-специфики: списки — `JSON`).

## Границы папок

| Владелец | Пути |
|---|---|
| backend | `pyproject.toml`, `alembic.ini`, `migrations/`, `app/main.py`, `app/__main__.py`, `app/config.py`, `app/logging_setup.py`, `app/db/`, `app/services/`, `app/scheduler/`, `app/middlewares/`, `scripts/`, `deploy/`, `tests/backend/`, `.env.example`, `README.md`, `.gitignore` |
| frontend | `app/bot/` (handlers, keyboards, texts, callbacks, states, notify), `tests/bot/` |
| designer | `docs/design.md` |

Frontend не трогает файлы backend и наоборот. Если frontend нужен новый сервис — пишет об этом в отчёте.

## Конфиг (.env)

`BOT_TOKEN`, `ADMIN_IDS` (через запятую tg_id админов), `ADMIN_CHAT_ID` (куда падают алерты, фидбэк ≤3, «Предложить место», брони без партнёра),
`DATABASE_URL`, `REDIS_URL` (пусто → MemoryStorage), `PHOTO_STORAGE` (`telegram` по умолчанию: храним file_id / URL), `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` (опц., заглушки),
`TZ=Asia/Yekaterinburg`, `PRIVACY_POLICY_URL`, `LOG_LEVEL=INFO`.
`app/config.py` → `settings: Settings` (dataclass) с полями выше; `settings.tz: ZoneInfo`.

## Модели (`app/db/models.py`)

- `Role(StrEnum)`: user, partner, admin. `Who`: couple, idea. `Budget`: lt1000, lt2000, unlimited.
- `Vibe`: romance, active, food, culture, unusual. `TimeOfDay`: day, evening, night.
- `BookingStatus`: pending, confirmed, declined, cancelled.
- `User`: id, tg_id (bigint, unique), username, first_name, role, who?, budget?, created_at, last_active_at, reactivation_sent_at?, onboarded (bool).
- `Scenario`: id, title, photos (JSON list[str] — file_id или URL, 2–5), location_name, address, map_link, vibes (JSON list[str]),
  price_text, price_value (int, ₽ на двоих, 0 = бесплатно), duration_text, duration_minutes (int), time_of_day, timing_text, includes_text,
  need_booking (bool), is_indoor (bool), is_featured, is_active, partner_id? → users.id, created_at, updated_at.
- `Slot`: id, scenario_id, starts_at (aware UTC), capacity, booked_count. Relationship `scenario`.
- `Booking`: id, user_id, slot_id, status, name, contact, created_at, confirmed_at?, declined_at?, cancelled_at?,
  reminded_24h_at?, reminded_3h_at?, feedback_requested_at?, attended (bool?). Relationships `user`, `slot` (→`slot.scenario`).
- `Feedback`: id, booking_id (unique), rating 1–5, comment?, created_at, updated_at.
- `Broadcast`: id, segment, text, created_by, sent_at?, sent_count.
- `SavedScenario`: user_id + scenario_id (PK), created_at.
- `Event`: id, user_id, type (`card_view`), scenario_id?, created_at — для статистики.

## Middlewares (`app/middlewares/`)

- `DbSessionMiddleware(sessionmaker)` → кладёт `session: AsyncSession` в `data`, коммитит после хендлера.
- `UserMiddleware` → upsert пользователя (tg_id, username, first_name, last_active_at=now, сброс reactivation_sent_at),
  роль admin если tg_id ∈ ADMIN_IDS; кладёт `user: User` в `data`.
- Фильтры ролей: `app/middlewares/roles.py` → `RoleFilter(*roles)` (aiogram Filter, смотрит `user.role`).

## Сервисы (`app/services/`, все `async`, первым аргументом `session`)

Все возвращают ORM-объекты с подгруженными нужными связями (selectinload), `expire_on_commit=False`.
Ошибки — исключения из `app/services/errors.py`: `NotFound`, `Forbidden`, `SlotFull`, `SlotInPast`, `InvalidState`, `ValidationError`.

**users.py**
- `set_profile(session, user, who: Who|None = ..., budget: Budget|None = ...) -> User`
- `mark_onboarded(session, user) -> User`
- `set_role(session, tg_id: int, role: Role) -> User` (создаёт юзера-заглушку, если нет)
- `list_partners(session) -> list[User]`

**scenarios.py**
- `@dataclass Filters`: vibe: Vibe|None, budget: `lt1000|1000_2000|2000_4000|unlimited`|None, time_of_day|None, date: `today|tomorrow|weekend|any`, need_booking: `yes|any`.
- `search(session, user, filters: Filters, offset=0, limit=5) -> tuple[list[Scenario], bool has_more]`
  только is_active; если в фильтре дата ≠ any — только сценарии со свободными слотами в этом окне (или need_booking=False);
  сортировка: is_featured desc → ближайший свободный слот asc (nulls last) → близость price_value к бюджету профиля.
- `weekend(session, user, offset=0, limit=5) -> tuple[list[Scenario], bool]` = search с date=weekend (ближайшие сб–вс; в сб/вс — текущие).
- `last_week_fallback(session, limit=5) -> list[Scenario]` — активные сценарии, у которых были слоты на прошлой неделе (для пустого состояния).
- `get(session, scenario_id, *, include_hidden=False) -> Scenario` (NotFound)
- `register_view(session, user, scenario_id)` — пишет Event card_view.
- `recommend(session, user, exclude_ids: list[int], limit=2) -> list[Scenario]` — «2 идеи на следующие выходные».
- `create(session, data: dict) -> Scenario`, `update(session, scenario_id, data: dict) -> Scenario`, `set_active(session, scenario_id, active: bool)`, `set_featured(...)`
  валидация: 2–5 фото, vibes ⊂ Vibe, обязательные поля → ValidationError.
- `list_all(session, offset, limit) -> tuple[list[Scenario], bool]` (для админки, включая скрытые).
- `list_for_partner(session, partner: User) -> list[Scenario]`.

**slots.py**
- `available(session, scenario_id, *, now=None) -> list[Slot]` — будущие, booked_count < capacity, по возрастанию.
- `create(session, scenario_id, starts_at: datetime (aware), capacity: int) -> Slot`
- `delete(session, slot_id)` (InvalidState если есть активные брони), `list_for_scenario(session, scenario_id) -> list[Slot]`.
- `list_for_partner(session, partner) -> list[Slot]` (будущие, только сценарии партнёра).

**saved.py**
- `toggle(session, user, scenario_id) -> bool` (True = теперь сохранён), `list_saved(session, user) -> list[Scenario]`, `is_saved(...) -> bool`.

**bookings.py**
- `create(session, user, slot_id, name, contact) -> Booking` — атомарно `UPDATE slots SET booked_count=booked_count+1 WHERE id=:id AND booked_count<capacity`
  (0 строк → `SlotFull`; прошедший слот → `SlotInPast`). Статус pending.
- `get(session, booking_id) -> Booking` (со slot.scenario.partner и user).
- `partner_chat_id(booking) -> int` — tg_id партнёра сценария, иначе `settings.ADMIN_CHAT_ID`.
- `confirm(session, booking_id, actor: User) -> Booking`, `decline(session, booking_id, actor: User) -> Booking`
  actor — admin или партнёр этого сценария (иначе Forbidden); только из pending (иначе InvalidState). decline освобождает место.
- `cancel(session, booking_id, user) -> Booking` — только владелец, только pending/confirmed, только до начала; освобождает место.
- `mark_attended(session, booking_id, actor, attended: bool) -> Booking`.
- `list_for_user(session, user) -> list[Booking]` (сначала будущие).
- `list_for_partner(session, partner, statuses=None) -> list[Booking]`, `list_pending(session) -> list[Booking]` (админ).

**feedback.py**
- `save_rating(session, booking_id, user, rating: int) -> Feedback` (upsert), `save_comment(session, booking_id, user, comment) -> Feedback`.
- `list_recent(session, limit=20, only_low=False) -> list[Feedback]` (с booking.slot.scenario, booking.user).

**stats.py**
- `collect(session, days=56) -> Stats` (dataclass): views, unique_viewers, bookings, conversion (bookings/unique viewers), attendance_rate,
  repeat_rate (пользователи с ≥2 confirmed / с ≥1), avg_rating, active_scenarios_week, top_view_share: list[(title, share)] (флаг >30%), indoor_active_count (<3 → предупреждение).

**broadcasts.py**
- Сегменты: `all`, `active` (активность ≤30 дн.), `couples` (who=couple), `with_bookings`, `no_bookings`.
- `segment_user_ids(session, segment) -> list[int]` (tg_id, только role=user), `create(session, segment, text, admin) -> Broadcast`, `mark_sent(session, broadcast_id, count)`.

## Notifier (интерфейс между планировщиком и ботом)

Backend объявляет протокол `app/scheduler/notifier.py`:
```python
class Notifier(Protocol):
    async def booking_reminder(self, booking: Booking, hours: int) -> bool: ...  # hours ∈ {24, 3}
    async def feedback_request(self, booking: Booking) -> bool: ...
    async def weekend_digest(self, tg_id: int, scenarios: list[Scenario]) -> bool: ...
    async def reactivation(self, tg_id: int) -> bool: ...
```
`True` = доставлено (False при TelegramForbiddenError и т.п. — не падаем). Frontend реализует `app/bot/notify.py: class TelegramNotifier(bot)`.

## Планировщик (`app/scheduler/jobs.py`, APScheduler AsyncIOScheduler, TZ из настроек)

- каждые 5 мин: напоминания 24 ч и 3 ч (confirmed, окна [starts_at-24h, starts_at-3h) и [starts_at-3h, starts_at), флаги reminded_*);
- каждые 10 мин: фидбэк — confirmed, `starts_at + duration_minutes + 2h <= now`, feedback_requested_at is null, не старше 3 дней;
- чт 18:00: дайджест сценариев выходных (до 5) — активным за 30 дней, role=user;
- ежедневно 12:00: реактивация — last_active_at < now-30d и reactivation_sent_at is null.
- `setup_scheduler(sessionmaker, notifier) -> AsyncIOScheduler`; функции джобов принимают `(sessionmaker, notifier, now=None)` — тестируемы без времени.

## Точка входа (`app/main.py`, backend)

`python -m app`: настраивает JSON-логи, движок, Dispatcher (RedisStorage если REDIS_URL, иначе MemoryStorage), middlewares,
`dp.include_routers(*app.bot.get_routers())`, `TelegramNotifier(bot)`, планировщик, `start_polling`.
Frontend обязан экспортировать `app/bot/__init__.py: get_routers() -> list[Router]` и `app/bot/notify.py: TelegramNotifier`.

## Хендлеры (frontend, `app/bot/handlers/`)

start (онбординг, deep-link `start=sc_<id>`), menu, catalog (выходные + фильтры + пагинация), card (карточка, сохранить, поделиться),
booking (FSM: слот → имя → контакт → подтверждение), my_bookings (список, отмена), saved, feedback (оценка, комментарий),
about (манифест + ссылка на политику ПД), suggest (предложить место → ADMIN_CHAT_ID), partner (входящие заявки, подтвердить/отклонить,
мои слоты, явка), admin (CRUD сценариев FSM, слоты, заявки, фидбэк, статистика, рассылка, назначение партнёра).
Приватность: контакт пользователя уходит только в чат партнёра конкретной брони (или админ-чат, если партнёра нет).
