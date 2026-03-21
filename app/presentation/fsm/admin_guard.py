"""Проверка, что пользователь в FSM админки (не перехватывать клиентские хендлеры)."""

from __future__ import annotations

from aiogram.fsm.context import FSMContext


async def is_active_admin_fsm(state: FSMContext) -> bool:
    st = await state.get_state()
    return bool(st and str(st).startswith("AdminStates:"))
