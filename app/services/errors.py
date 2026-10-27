"""Доменные ошибки сервисов. Хендлеры ловят их и показывают человеку понятный текст."""


class ServiceError(Exception):
    """Базовая ошибка сервисного слоя."""


class NotFound(ServiceError):
    pass


class Forbidden(ServiceError):
    pass


class SlotFull(ServiceError):
    pass


class SlotInPast(ServiceError):
    pass


class InvalidState(ServiceError):
    pass


class ValidationError(ServiceError):
    """args[0] — человекочитаемое сообщение (по-русски)."""
