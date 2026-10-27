# Итоговый API backend (для frontend)

Источник правды — код `app/services/`, `app/db/models.py`, `app/scheduler/notifier.py`, `app/middlewares/`. Ниже — сводка.

## Общее
- Сервисы делают только `flush`; коммит — `DbSessionMiddleware`. Время aware UTC. Kw `now` — только для тестов.
- Bot создан с `parse_mode=HTML` по умолчанию → пользовательский ввод экранировать `html.escape`.
- В хендлерах доступны аргументы: `session`, `user`, `sessionmaker`, `settings`, `notifier`, `bot`, `state`.
- `UserMiddleware` — outer, до фильтров; `RoleFilter(Role.admin)` из `app.middlewares`.
- Роль admin выставляется автоматически по `ADMIN_IDS`.

## Модели (`app.db.models`)
Enums (StrEnum): Role(user, partner, admin), Who(couple, idea), Budget(lt1000, lt2000, unlimited), Vibe(romance, active, food, culture, unusual), TimeOfDay(day, evening, night), BookingStatus(pending, confirmed, declined, cancelled), EventType(card_view).
- User: id, tg_id, username?, first_name?, role, who?, budget?, created_at, last_active_at, reactivation_sent_at?, privacy_accepted_at?, onboarded.
- Scenario: id, title, photos: list[str], location_name, address, map_link?, vibes: list[str], price_text, price_value, duration_text, duration_minutes, time_of_day, timing_text?, includes_text?, need_booking, is_indoor, is_featured, is_active, partner_id?, created_at, updated_at. `.partner` eager; `.slots` — lazy="raise" (брать через сервисы slots).
- Slot: id, scenario_id, starts_at, capacity, booked_count, `.free`; `.scenario(.partner)` eager.
- Booking: id, user_id, slot_id, status, name, contact, created_at, confirmed_at?, declined_at?, cancelled_at?, reminded_24h_at?, reminded_3h_at?, feedback_requested_at?, overdue_alerted_at?, attended?; `.user`, `.slot.scenario.partner`, `.feedback` eager.
- Feedback: id, booking_id, rating, comment?, created_at, updated_at; `.booking` eager.
- Broadcast, SavedScenario(.scenario), Event.

## Ошибки (`app.services.errors`)
`ServiceError` ← NotFound, Forbidden, SlotFull, SlotInPast, InvalidState, ValidationError (`args[0]` — сообщение по-русски).

## users
```
get_by_tg_id(session, tg_id) -> User | None
set_profile(session, user, who=..., budget=...) -> User   # ... = не менять, None = сбросить
mark_onboarded(session, user) -> User                      # и для deep-link start=sc_<id>
accept_privacy(session, user) -> User
set_role(session, tg_id, role) -> User
list_partners(session) -> list[User]
```
## scenarios
```
Filters(vibe=None, budget: "lt1000"|"1000_2000"|"2000_4000"|"unlimited"|None=None, time_of_day=None, date: "today"|"tomorrow"|"weekend"|"any"="any", need_booking: "yes"|"any"="any")
search(session, user, filters, offset=0, limit=5, *, exclude_ids=None) -> (list[Scenario], has_more)
weekend(session, user, offset=0, limit=5) -> (list[Scenario], has_more)
last_week_fallback(session, limit=5) -> list[Scenario]
get(session, scenario_id, *, include_hidden=False) -> Scenario
register_view(session, user, scenario_id)
recommend(session, user, exclude_ids, limit=2) -> list[Scenario]
create(session, data) -> Scenario            # без is_active → СКРЫТ
update(session, scenario_id, data) -> Scenario   # частичное
set_active(session, id, bool), set_featured(session, id, bool)
list_all(session, offset=0, limit=10) -> (list, has_more)    # включая скрытые
list_for_partner(session, partner) -> list[Scenario]
```
create/update: обязательны title, photos (2–5), location_name, address, vibes (⊂Vibe), price_text, price_value (int≥0), duration_text, duration_minutes (int>0), time_of_day; опц. map_link, timing_text, includes_text, need_booking, is_indoor, is_featured, is_active, partner_id. Неизвестные ключи → ValidationError.

## slots
```
get(session, slot_id) -> Slot
available(session, scenario_id) -> list[Slot]
create(session, scenario_id, starts_at (aware), capacity) -> Slot   # ValidationError: naive / прошлое / capacity<1
delete(session, slot_id)                                           # InvalidState при активных бронях
list_for_scenario(session, scenario_id) -> list[Slot]
list_for_partner(session, partner) -> list[Slot]
```
## saved
`toggle(session, user, scenario_id) -> bool`, `is_saved(...) -> bool`, `list_saved(session, user) -> list[Scenario]`.

## bookings
```
create(session, user, slot_id, name, contact) -> Booking   # ValidationError(имя 1–100, контакт 3–100), NotFound, SlotInPast, InvalidState(уже есть бронь на слот), SlotFull
get(session, booking_id) -> Booking
partner_chat_id(booking) -> int | None   # партнёр или ADMIN_CHAT_ID
confirm / decline(session, booking_id, actor) -> Booking     # Forbidden, InvalidState (не pending)
cancel(session, booking_id, user) -> Booking                 # Forbidden, InvalidState (статус/уже началось)
mark_attended(session, booking_id, actor, attended: bool) -> Booking   # только confirmed
list_for_user(session, user) -> list[Booking]
list_for_partner(session, partner, statuses=None) -> list[Booking]
list_pending(session) -> list[Booking]
```
## feedback
`save_rating(session, booking_id, user, rating)`, `save_comment(session, booking_id, user, comment)` (без оценки → InvalidState), `list_recent(session, limit=20, only_low=False)`.

## stats
`collect(session, days=56) -> Stats(days, views, unique_viewers, bookings, conversion?, attendance_rate?, repeat_rate?, avg_rating?, active_scenarios_week, top_view_share: list[(title, share)], indoor_active_count; props top_view_alert, indoor_warning)`. None = нет данных.

## broadcasts
```
SEGMENTS = ("all", "active", "couples", "with_bookings", "no_bookings")
segment_user_ids(session, segment) -> list[int]
create(session, segment, text, admin) -> Broadcast
send_broadcast(sessionmaker, bot, broadcast_id, *, delay=0.05) -> int
```
Использование: `bc = await broadcasts.create(...)`; `await session.commit()`; `asyncio.create_task(broadcasts.send_broadcast(sessionmaker, bot, bc.id))`. Текст уходит plain (parse_mode=None).

## Notifier → реализовать `app/bot/notify.py: TelegramNotifier(bot)`
```
booking_reminder(booking, hours: 24|3) -> bool
feedback_request(booking) -> bool
weekend_digest(tg_id, scenarios) -> bool
reactivation(tg_id) -> bool
overdue_booking_alert(booking) -> bool   # в ADMIN_CHAT_ID
```
False = не доставлено (Forbidden и т.п.), исключения не бросать.

## Точка входа
`app/main.py` импортирует `from app.bot import get_routers` и `from app.bot.notify import TelegramNotifier`.
