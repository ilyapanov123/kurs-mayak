"""Типы данных и ошибки, которыми обмениваются модули приложения."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID


type PairKind = Literal["fiat", "crypto"]
type ConversionDirection = Literal["to_quote", "to_base"]
type AlertCondition = Literal["ge", "le"]
type HistoryDays = Literal[1, 7, 30]
type SeriesKind = Literal["live", "hour"]
type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


def _require_positive(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} должно иметь тип Decimal")
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} должно быть конечным положительным числом")


@dataclass(frozen=True, slots=True)
class AssetPair:
    id: UUID
    base_code: str
    quote_code: str
    kind: PairKind


@dataclass(frozen=True, slots=True)
class Quote:
    pair_id: UUID
    price: Decimal
    source: str
    quoted_at: datetime
    received_at: datetime

    def __post_init__(self) -> None:
        _require_positive(self.price, "price")
        if self.quoted_at.utcoffset() is None or self.received_at.utcoffset() is None:
            raise ValueError("Время котировки и получения должно содержать часовой пояс")


@dataclass(frozen=True, slots=True)
class ConversionResult:
    pair_id: UUID
    direction: ConversionDirection
    input_amount: Decimal
    output_amount: Decimal
    quote: Quote
    display_value: str


@dataclass(frozen=True, slots=True)
class DynamicsResult:
    pair_id: UUID
    period_start: datetime
    period_end: datetime
    start_price: Decimal
    end_price: Decimal
    absolute_change: Decimal
    percent_change: Decimal
    minimum: Decimal
    maximum: Decimal
    direction: Literal["up", "down", "unchanged"]
    sample_count: int
    source: str


@dataclass(frozen=True, slots=True)
class Alert:
    id: UUID
    user_id: UUID
    pair_id: UUID
    condition: AlertCondition
    threshold: Decimal
    active: bool = True
    last_match: bool | None = None
    last_quote_at: datetime | None = None
    trigger_seq: int = 0
    version: int = 0

    def __post_init__(self) -> None:
        _require_positive(self.threshold, "threshold")


@dataclass(frozen=True, slots=True)
class AlertState:
    """Данные для проверки порога без обращения к БД или Telegram."""

    alert: Alert
    pair: AssetPair
    chat_id: int


@dataclass(frozen=True, slots=True)
class TriggerEvent:
    id: UUID
    alert_id: UUID
    trigger_seq: int
    pair: AssetPair
    price: Decimal
    threshold: Decimal
    condition: AlertCondition
    detected_at: datetime
    quoted_at: datetime
    chat_id: int

type OutboxStatus = Literal[
    "pending", "sending", "sent", "retry", "failed", "cancelled", "unknown"
]


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """(HLD 6.1, 8.1)."""

    id: UUID
    alert_id: UUID
    trigger_seq: int
    payload: dict[str, JsonValue]
    status: OutboxStatus
    attempts: int
    next_attempt_at: datetime | None
    lease_until: datetime | None
    sent_at: datetime | None
    telegram_message_id: int | None
    last_error_code: str | None

    def __post_init__(self) -> None:
        if self.attempts < 0:
            raise ValueError("attempts не может быть отрицательным")


def trigger_payload(event: TriggerEvent) -> dict[str, JsonValue]:
    """ (HLD 4.3)."""
    return {
        "alert_id": str(event.alert_id),
        "trigger_seq": event.trigger_seq,
        "pair_id": str(event.pair.id),
        "base_code": event.pair.base_code,
        "quote_code": event.pair.quote_code,
        "kind": event.pair.kind,
        "price": str(event.price),
        "threshold": str(event.threshold),
        "condition": event.condition,
        "detected_at": event.detected_at.isoformat(),
        "quoted_at": event.quoted_at.isoformat(),
        "chat_id": event.chat_id,
    }

@dataclass(frozen=True, slots=True)
class DialogSession:
    user_id: UUID
    state: str
    payload: dict[str, JsonValue]
    version: int


class ErrorCode(StrEnum):
    UNSUPPORTED_ASSET = "UnsupportedAsset"
    INVALID_PAIR = "InvalidPair"
    INVALID_NUMBER = "InvalidNumber"
    NON_POSITIVE_VALUE = "NonPositiveValue"
    PRECISION_EXCEEDED = "PrecisionExceeded"
    QUOTE_UNAVAILABLE = "QuoteUnavailable"
    STALE_QUOTE = "StaleQuote"
    HISTORY_INCOMPLETE = "HistoryIncomplete"
    NOT_FOUND = "NotFound"
    CONFLICT = "Conflict"


class AppError(Exception):
    """Ошибка сервиса с сообщением для пользователя и идентификатором операции."""

    def __init__(
        self, code: ErrorCode, user_message: str, *, retryable: bool, correlation_id: UUID
    ) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.retryable = retryable
        self.correlation_id = correlation_id
