from __future__ import annotations


class AppError(Exception):
    """
    Base class for application-domain errors.

    Handlers should translate these into user-friendly Telegram messages.
    """


class UserInputError(AppError):
    """User provided invalid input (reply with safe message)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class NotFoundError(AppError):
    """Entity not found (e.g. stale draft)."""

    def __init__(self, message: str = "Сессия устарела. Начните заново: /start") -> None:
        super().__init__(message)


class ConflictError(AppError):
    """Conflict in business state (e.g. slot already taken)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SystemError(AppError):
    """Unexpected internal error."""

    def __init__(self, message: str = "Произошла ошибка. Попробуйте ещё раз позже.") -> None:
        super().__init__(message)


def error_to_user_message(err: Exception) -> str:
    """
    Maps application exceptions to safe Telegram texts.
    """

    if isinstance(err, UserInputError):
        return str(err)
    if isinstance(err, NotFoundError):
        return str(err)
    if isinstance(err, ConflictError):
        return str(err)
    if isinstance(err, SystemError):
        return str(err)
    # Fallback: don't leak stack traces to users.
    return "Произошла ошибка. Попробуйте ещё раз позже."

