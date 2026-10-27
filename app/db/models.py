from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UTCDateTime, utcnow


class Role(StrEnum):
    user = "user"
    partner = "partner"
    admin = "admin"


class Who(StrEnum):
    couple = "couple"
    idea = "idea"


class Budget(StrEnum):
    lt1000 = "lt1000"
    lt2000 = "lt2000"
    unlimited = "unlimited"


class Vibe(StrEnum):
    romance = "romance"
    active = "active"
    food = "food"
    culture = "culture"
    unusual = "unusual"


class TimeOfDay(StrEnum):
    day = "day"
    evening = "evening"
    night = "night"


class BookingStatus(StrEnum):
    pending = "pending"
    confirmed = "confirmed"
    declined = "declined"
    cancelled = "cancelled"


class EventType(StrEnum):
    card_view = "card_view"


def _enum(cls: type[StrEnum], name: str) -> Enum:
    # VARCHAR без нативного PG-типа: одинаково на SQLite и Postgres
    return Enum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=False,
        length=16,
        values_callable=lambda e: [m.value for m in e],
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(_enum(Role, "role"), default=Role.user)
    who: Mapped[Who | None] = mapped_column(_enum(Who, "who"))
    budget: Mapped[Budget | None] = mapped_column(_enum(Budget, "budget"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_active_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    reactivation_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)

    def __repr__(self) -> str:
        return f"User(id={self.id}, tg_id={self.tg_id}, role={self.role})"


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    photos: Mapped[list[str]] = mapped_column(JSON, default=list)
    location_name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300))
    map_link: Mapped[str | None] = mapped_column(String(500))
    vibes: Mapped[list[str]] = mapped_column(JSON, default=list)
    price_text: Mapped[str] = mapped_column(String(200))
    price_value: Mapped[int] = mapped_column(Integer, default=0)
    duration_text: Mapped[str] = mapped_column(String(100))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=120)
    time_of_day: Mapped[TimeOfDay] = mapped_column(_enum(TimeOfDay, "time_of_day"))
    timing_text: Mapped[str | None] = mapped_column(Text)
    includes_text: Mapped[str | None] = mapped_column(Text)
    need_booking: Mapped[bool] = mapped_column(Boolean, default=True)
    is_indoor: Mapped[bool] = mapped_column(Boolean, default=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    partner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    partner: Mapped[User | None] = relationship(lazy="selectin")
    slots: Mapped[list[Slot]] = relationship(
        back_populates="scenario", lazy="raise", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"Scenario(id={self.id}, title={self.title!r})"


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="capacity_positive"),
        CheckConstraint("booked_count >= 0", name="booked_nonneg"),
        UniqueConstraint("scenario_id", "starts_at", name="uq_slots_scenario_starts_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), index=True
    )
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    booked_count: Mapped[int] = mapped_column(Integer, default=0)

    scenario: Mapped[Scenario] = relationship(back_populates="slots", lazy="selectin")
    bookings: Mapped[list[Booking]] = relationship(
        back_populates="slot", lazy="raise", passive_deletes=True
    )

    @property
    def free(self) -> int:
        return max(self.capacity - self.booked_count, 0)

    def __repr__(self) -> str:
        return (
            f"Slot(id={self.id}, starts_at={self.starts_at}, {self.booked_count}/{self.capacity})"
        )


_ACTIVE_BOOKING = text("status IN ('pending', 'confirmed')")


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        # одна активная заявка юзера на слот — защита от двойного нажатия на уровне БД
        Index(
            "uq_bookings_active_user_slot",
            "user_id",
            "slot_id",
            unique=True,
            postgresql_where=_ACTIVE_BOOKING,
            sqlite_where=_ACTIVE_BOOKING,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id", ondelete="CASCADE"), index=True)
    status: Mapped[BookingStatus] = mapped_column(
        _enum(BookingStatus, "booking_status"), default=BookingStatus.pending, index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    contact: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    declined_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    reminded_24h_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    reminded_3h_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    feedback_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    overdue_alerted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    attended: Mapped[bool | None] = mapped_column(Boolean)

    user: Mapped[User] = relationship(lazy="selectin")
    slot: Mapped[Slot] = relationship(back_populates="bookings", lazy="selectin")
    feedback: Mapped[Feedback | None] = relationship(
        back_populates="booking", lazy="selectin", uselist=False
    )

    def __repr__(self) -> str:
        return f"Booking(id={self.id}, slot_id={self.slot_id}, status={self.status})"


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), unique=True
    )
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    booking: Mapped[Booking] = relationship(back_populates="feedback", lazy="selectin")


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    segment: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)


class SavedScenario(Base):
    __tablename__ = "saved_scenarios"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    scenario: Mapped[Scenario] = relationship(lazy="selectin")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_type_created_at", "type", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    type: Mapped[str] = mapped_column(String(32))
    scenario_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
