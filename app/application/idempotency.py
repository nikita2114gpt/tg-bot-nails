from typing import Callable, TypeVar, Optional


T = TypeVar("T")


def create_appointment_once(
    draft_id: str,
    get_existing_fn: Callable[[str], Optional[T]],
    create_fn: Callable[[], T],
) -> T:
    existing = get_existing_fn(draft_id)
    if existing is not None:
        return existing

    return create_fn()


def create_outbox_once(
    idempotency_key: str,
    get_existing_fn: Callable[[str], Optional[T]],
    create_fn: Callable[[], T],
) -> T:
    existing = get_existing_fn(idempotency_key)
    if existing is not None:
        return existing

    return create_fn()