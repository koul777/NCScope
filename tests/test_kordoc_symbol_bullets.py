"""Kordoc 4.24.1 maps symbol-font (Wingdings) bullets to Unicode.

4.9.1 dropped an HWP bullet such as U+F0FA and left a blank; 4.24.1 renders
the same glyph as ``🢭`` (U+1F8AD). The ability units of 303898·303954 (dev)
then appeared twice, once with a ``🢭 `` prefix. The bullet is the same
symbol NCScope already strips as ``\\uf0fa``.
"""

from __future__ import annotations

from app.services.kordoc_parser import structure_job_description

_ROWS = (
    "| 채용분야 | 기간제 근로자 – 방사선사(영상의학과) |\n"
    "| --- | --- |\n"
    "| 능력단위 | {b}방사선사의 업무(방사선검사 업무, 예약 및 상담 등)<br>{b}환자 교육 및 관리"
    "<br>{b}방사선 검사 및 피폭 관리<br>{b}정도 관리, 영상 관리 및 장비 관리 |\n"
    "| 필요자격 | {b}방사선사 면허증 소지자 |\n"
)


def _unit_names(markdown: str) -> list[str]:
    fields = structure_job_description({"markdown": markdown}, filename="jd.hwp")["fields"]
    names = []
    for unit in fields.get("ability_units") or []:
        name = unit.get("name") if isinstance(unit, dict) else unit
        names.append(str(name or ""))
    return names


def test_kordoc_4_24_symbol_bullet_gives_the_same_ability_units_as_4_9() -> None:
    old_shape = _unit_names(_ROWS.format(b=" "))
    new_shape = _unit_names(_ROWS.format(b="\U0001F8AD "))

    assert old_shape
    assert new_shape == old_shape
    assert not any("\U0001F8AD" in name for name in new_shape)


def test_private_use_and_unicode_forms_of_the_bullet_are_equivalent() -> None:
    assert _unit_names(_ROWS.format(b=" ")) == _unit_names(
        _ROWS.format(b="\U0001F8AD ")
    )
