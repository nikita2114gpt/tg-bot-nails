from __future__ import annotations

from typing import Optional


def _split(raw: str, prefix: str) -> list[str]:
    if not raw or not isinstance(raw, str):
        raise ValueError("bad callback")
    if not raw.startswith(prefix):
        raise ValueError("bad callback")
    rest = raw[len(prefix) :]
    if not rest.startswith("|"):
        raise ValueError("bad callback")
    return rest[1:].split("|")


def build_client_cancel_my(appointment_id: str) -> str:
    return f"c1|x|{appointment_id}"


def parse_client(raw: str) -> tuple[str, list[str]]:
    parts = _split(raw, "c1")
    if not parts or not parts[0]:
        raise ValueError("bad callback")
    return parts[0], parts[1:]


def build_admin_home() -> str:
    return "a1|home"


def build_admin_records() -> str:
    return "a1|rec"


def build_admin_cancelled() -> str:
    return "a1|cn"


def build_admin_search() -> str:
    return "a1|sr"


def build_admin_month(month_shift: int = 0) -> str:
    return f"a1|mon|{month_shift}"


def build_admin_month_date(ymd: str) -> str:
    return f"a1|md|{ymd}"


def build_admin_time_slot(ymd: str, hhmm: str) -> str:
    return f"a1|ts|{ymd}|{hhmm}"


def build_admin_open(appt_id: str) -> str:
    return f"a1|p|{appt_id}"


def build_admin_cancel_appt(appt_id: str) -> str:
    return f"a1|x|{appt_id}"


def build_admin_edit_menu(appt_id: str) -> str:
    return f"a1|e|{appt_id}"


def build_admin_set_service(appt_id: str, service_index: int) -> str:
    return f"a1|s|{appt_id}|{service_index}"


def build_admin_set_date(appt_id: str, ymd: str) -> str:
    return f"a1|dt|{appt_id}|{ymd}"


def build_admin_set_time(appt_id: str, hhmm: str) -> str:
    return f"a1|tm|{appt_id}|{hhmm}"


def build_admin_back_list(kind: str, payload: Optional[str] = None) -> str:
    if payload is None:
        return f"a1|bk|{kind}"
    return f"a1|bk|{kind}|{payload}"


def build_admin_edit_name(appt_id: str) -> str:
    return f"a1|nm|{appt_id}"


def build_admin_edit_phone(appt_id: str) -> str:
    return f"a1|ph|{appt_id}"


def build_admin_ops_schedule() -> str:
    return "a1|ops_sc"


def build_admin_ops_services() -> str:
    return "a1|ops_sv"


def build_admin_ops_blacklist() -> str:
    return "a1|ops_bl"


def build_admin_ops_schedule_edit(field: str) -> str:
    return f"a1|ops_sc_ed|{field}"


def build_admin_ops_schedule_service_step_menu() -> str:
    return "a1|ops_sc_ssm"


def build_admin_ops_schedule_service_step_set(service_id: str) -> str:
    return f"a1|ops_sc_ss|{service_id}"


def build_admin_day_toggle(date_yyyymmdd: str, open_day: bool) -> str:
    return f"a1|ops_day_tg|{date_yyyymmdd}|{1 if open_day else 0}"


def build_admin_day_edit(date_yyyymmdd: str, field: str) -> str:
    return f"a1|ops_day_ed|{date_yyyymmdd}|{field}"


def build_admin_move_start(appt_id: str) -> str:
    return f"a1|mv|{appt_id}"


def build_admin_move_date(appt_id: str, date_yyyymmdd: str) -> str:
    return f"a1|mvd|{appt_id}|{date_yyyymmdd}"


def build_admin_move_time(appt_id: str, date_yyyymmdd: str, hhmm: str) -> str:
    return f"a1|mvt|{appt_id}|{date_yyyymmdd}|{hhmm}"


def build_admin_ops_salon() -> str:
    return "a1|ops_si"


def build_admin_ops_salon_edit(field: str) -> str:
    return f"a1|ops_si_ed|{field}"


def build_admin_ops_salon_toggle(field: str) -> str:
    return f"a1|ops_si_tg|{field}"


def build_admin_ops_service_open(service_id: str) -> str:
    return f"a1|ops_sv_o|{service_id}"


def build_admin_ops_service_add() -> str:
    return "a1|ops_sv_add"


def build_admin_ops_service_toggle(service_id: str) -> str:
    return f"a1|ops_sv_tg|{service_id}"


def build_admin_ops_service_deactivate(service_id: str) -> str:
    return f"a1|ops_sv_off|{service_id}"


def build_admin_ops_service_edit(service_id: str, field: str) -> str:
    return f"a1|ops_sv_ed|{service_id}|{field}"


def build_admin_ops_blacklist_add() -> str:
    return "a1|ops_bl_add"


def build_admin_ops_blacklist_deactivate(entry_id: str) -> str:
    return f"a1|ops_bl_off|{entry_id}"


def build_admin_blacklist_from_appointment(appt_id: str) -> str:
    return f"a1|bl_ap|{appt_id}"


def build_admin_cancelled_page(page: int) -> str:
    return f"a1|cnp|{page}"


def build_admin_ops_service_delete(service_id: str) -> str:
    return f"a1|ops_sv_del|{service_id}"


def parse_admin(raw: str) -> tuple[str, list[str]]:
    parts = _split(raw, "a1")
    if not parts or not parts[0]:
        raise ValueError("bad callback")
    return parts[0], parts[1:]
