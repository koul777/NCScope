from __future__ import annotations

import copy
import json
import os
import re
from typing import Any

from app.services.openai_http import post_chat_completions_with_retries
from app.settings import settings


_ENTRY_LEVEL_TRIGGER_RE = re.compile(
    r"(수행\s*경험|경험이\s*있다면|해본\s*경험|참여했던|담당했던|실무에서|업무를\s*수행|수립한\s*경험|운영한\s*경험)"
)
_ENTRY_LEVEL_ALREADY_RE = re.compile(r"(유사\s*사례|가정\s*상황|가정해|가정하여|가정하고)")


def _soften_entry_level_question(question: str) -> str:
    q = str(question or "").strip()
    if not q:
        return q
    if _ENTRY_LEVEL_ALREADY_RE.search(q):
        return q
    if not _ENTRY_LEVEL_TRIGGER_RE.search(q):
        return q

    replacements: list[tuple[str, str]] = [
        (r"수행\s*경험에서", "수행했거나 유사 상황을 가정한 사례에서"),
        (r"수행\s*경험을", "수행했거나 유사 상황을 가정한 사례를"),
        (r"수립한\s*경험", "수립했거나 유사 상황을 가정한 사례"),
        (r"운영한\s*경험", "운영했거나 유사 상황을 가정한 사례"),
        (r"경험이\s*있다면", "경험이나 유사 사례(가정 상황 포함)가 있다면"),
        (r"경험에\s*대해", "경험 또는 유사 사례(가정 상황 포함)에 대해"),
        (r"참여했던", "참여했거나 유사한"),
        (r"담당했던", "담당했거나 유사한"),
    ]
    out = q
    for pattern, repl in replacements:
        new_q = re.sub(pattern, repl, out, count=1)
        if new_q != out:
            out = new_q
            break
    if out == q:
        out = re.sub(r"경험", "경험 또는 유사 사례(가정 상황 포함)", q, count=1)
    return out


def _build_question_generation_prompt(
    ncs_matches: list[dict[str, Any]],
    ncs_ksa: list[dict[str, Any]] | None = None,
    jd_text: str = "",
    strengths: str = "",
    mode: str = "diverse",
    target_count: int = 6,
    extra_context: str = "",
) -> str:
    ncs_lines: list[str] = []
    for row in (ncs_matches or [])[:8]:
        code = str(row.get("ncsClCd", "")).strip()
        name = str(row.get("compeUnitName", "")).strip()
        desc = str(row.get("compeUnitDef", "")).strip()
        if code and name:
            ncs_lines.append(f"- {code} | {name} | {desc[:220]}")

    ksa_lines: list[str] = []
    seen_ksa: set[str] = set()
    for row in (ncs_ksa or [])[:40]:
        factor = str(row.get("factorName", "")).strip()
        if not factor:
            continue
        norm = re.sub(r"\s+", "", factor)
        if norm in seen_ksa:
            continue
        seen_ksa.add(norm)
        src = str(row.get("factorSource", "")).strip()
        unit = str(row.get("compeUnitName", "")).strip()
        ksa_lines.append(f"- {factor} | unit={unit} | source={src}")

    mode_hint = {
        "ncs_code_only": "NCS 肄붾뱶 以묒떖 援ъ“??硫댁젒",
        "diverse": "?ㅼ뼇???좏삎??援ъ“??硫댁젒",
        "personalized": "吏?먯옄 留λ씫 諛섏쁺 援ъ“??硫댁젒",
        "ksa_driven": "KSA 吏곸젒 寃利?援ъ“??硫댁젒",
        "local_pack": "JD+KSA ?듯빀 援ъ“??硫댁젒",
    }.get(mode, "援ъ“??硫댁젒")

    return (
        "?꾨옒 而⑦뀓?ㅽ듃濡?援ъ“??硫댁젒 吏덈Ц???앹꽦?섏꽭??\n"
        f"紐⑤뱶: {mode_hint}\n"
        f"?앹꽦 媛쒖닔: {target_count}\n\n"
        "[洹쒖튃]\n"
        "- 諛섎뱶???쒓뎅?대줈 ?묒꽦\n"
        "- 吏덈Ц? STAR ?묐떟???좊룄?댁빞 ??n"
        "- 媛?吏덈Ц? ?⑥씪 ??웾留?寃利?n"
        "- 媛?吏덈Ц留덈떎 follow_ups 3媛쒕? 諛섎뱶???ы븿\n"
        "- follow_ups??瑗щ━臾쇨린 援ъ“: 二쇱쭏臾멤넂瑗щ━1?믨섕由??믨섕由? ?쒖꽌濡????듬???諛쏆븘????源딆씠 ?뚭퀬?쒕뒗 吏덈Ц\n"
        "- 瑗щ━吏덈Ц?쇰━ ?댁슜??寃뱀튂硫????? 媛곴컖 evaluation_points???ㅻⅨ ??ぉ??寃利?n"
        "- evaluation_points??4~6媛? 痢≪젙 媛?ν븳 臾몄옣\n"
        "- ksa_refs?먮뒗 ?대떦 吏덈Ц怨?吏곸젒 ?곌껐??KSA ?ㅼ썙??2~4媛?n"
        "- 誘쇨컧/李⑤퀎 吏덈Ц 湲덉?\n\n"
        "[吏덈Ц ?좏삎 鍮꾩쑉]\n"
        "- 寃쏀뿕: 50%\n"
        "- ?곹솴: 30%\n"
        "- 吏곷Т吏?? 20%\n\n"
        "[異쒕젰 ?뺤떇]\n"
        "JSON 媛앹껜 ?섎굹留?異쒕젰:\n"
        "{\n"
        '  "interview_questions": [\n'
        "    {\n"
        '      "type": "寃쏀뿕|?곹솴|吏곷Т吏??,\n'
        '      "competency": "?λ젰?⑥쐞紐?,\n'
        '      "ncsClCd": "肄붾뱶",\n'
        '      "question": "二쇱쭏臾?,\n'
        '      "follow_ups": ["?щ?援ъ껜??, "?대젮?/?泥?, "寃곌낵/援먰썕"],\n'
        '      "evaluation_points": ["??ぉ1", "??ぉ2", "??ぉ3", "??ぉ4"],\n'
        '      "ksa_refs": ["KSA1", "KSA2"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "[NCS]\n"
        f"{chr(10).join(ncs_lines) if ncs_lines else '- ?놁쓬'}\n\n"
        "[KSA]\n"
        f"{chr(10).join(ksa_lines) if ksa_lines else '- ?놁쓬'}\n\n"
        + (f"[JD]\n{jd_text[:1500]}\n\n" if jd_text else "")
        + (f"[媛뺤젏/?꾨줈??\n{strengths[:1500]}\n\n" if strengths else "")
        + (f"[異붽?而⑦뀓?ㅽ듃]\n{extra_context[:1500]}\n" if extra_context else "")
    )


def _extract_json_text(response_text: str) -> str:
    txt = str(response_text or "").strip()
    if not txt:
        return ""
    block = re.search(r"```(?:json)?\s*([\s\S]*?)```", txt)
    if block:
        return block.group(1).strip()
    start_obj = txt.find("{")
    start_arr = txt.find("[")
    starts = [x for x in (start_obj, start_arr) if x >= 0]
    if not starts:
        return txt
    return txt[min(starts):].strip()


def _slice_balanced_json(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""

    start_obj = raw.find("{")
    start_arr = raw.find("[")
    starts = [x for x in (start_obj, start_arr) if x >= 0]
    if not starts:
        return ""
    start = min(starts)

    stack: list[str] = []
    in_string = False
    escaped = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if in_string:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "{[":
            stack.append(ch)
            continue
        if ch in "}]":
            if not stack:
                continue
            open_ch = stack.pop()
            if (open_ch == "{" and ch != "}") or (open_ch == "[" and ch != "]"):
                return ""
            if not stack:
                return raw[start: idx + 1].strip()
    return ""


def _extract_message_content(data: dict[str, Any]) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        return ""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            txt = str(part.get("text", "")).strip()
            if txt:
                parts.append(txt)
        return "\n".join(parts)
    return str(content or "")


def _normalize_question_item(item: dict[str, Any]) -> dict[str, Any] | None:
    question = _soften_entry_level_question(str(item.get("question", "")).strip())
    if not question:
        return None

    raw_follow_ups = item.get("follow_ups")
    follow_ups: list[str] = []
    if isinstance(raw_follow_ups, list):
        follow_ups = [_soften_entry_level_question(str(x).strip()) for x in raw_follow_ups if str(x).strip()]
    else:
        single = str(item.get("follow_up", "")).strip()
        if single:
            follow_ups = [_soften_entry_level_question(single)]
    if len(follow_ups) < 3:
        fallback_fus = [
            "洹??곹솴?먯꽌 蹂몄씤??留≪? 援ъ껜?곸씤 ??븷怨??먮떒 洹쇨굅瑜?留먯???二쇱꽭??",
            "洹?怨쇱젙?먯꽌 媛???대젮?좊뜕 遺遺꾩? 臾댁뾿?닿퀬, ?대뼸寃??닿껐?섏뀲?섏슂?",
            "洹?寃곌낵???대븷怨? ?뚯씠耳쒕낫硫??대뼡 ?먯쓣 ?ㅻⅤ寃??섏떆寃좎뒿?덇퉴?",
        ]
        for f in fallback_fus:
            if len(follow_ups) >= 3:
                break
            follow_ups.append(f)
    follow_ups = follow_ups[:3]

    ev = item.get("evaluation_points")
    evaluation_points = [str(x).strip() for x in (ev or []) if str(x).strip()] if isinstance(ev, list) else []
    if len(evaluation_points) < 4:
        defaults = [
            "?곹솴 留λ씫??援ъ“?곸쑝濡??ㅻ챸?섎뒗媛",
            "?듭떖 ?섏궗寃곗젙 洹쇨굅媛 紐낇솗?쒓?",
            "?ㅽ뻾 怨쇱젙怨??묒뾽 諛⑹떇??援ъ껜?곸씤媛",
            "?깃낵? ?숈뒿 ?ъ씤?몃? ?섏튂 ?먮뒗 ?ъ떎濡??쒖떆?섎뒗媛",
        ]
        for d in defaults:
            if len(evaluation_points) >= 4:
                break
            evaluation_points.append(d)
    evaluation_points = evaluation_points[:6]

    ksa = item.get("ksa_refs")
    ksa_refs = [str(x).strip() for x in (ksa or []) if str(x).strip()] if isinstance(ksa, list) else []

    return {
        "question": question,
        "type": str(item.get("type", "寃쏀뿕")).strip() or "寃쏀뿕",
        "competency": str(item.get("competency", "")).strip(),
        "ncsClCd": str(item.get("ncsClCd", "")).strip(),
        "evaluation_points": evaluation_points,
        "follow_ups": follow_ups,
        "follow_up": follow_ups[0],
        "ksa_refs": ksa_refs,
    }


def _parse_openai_response(response_text: str) -> list[dict[str, Any]]:
    raw = _extract_json_text(response_text)
    candidates = [raw, _slice_balanced_json(raw), _slice_balanced_json(str(response_text or ""))]
    data: Any | None = None
    seen_candidates: set[str] = set()
    for candidate in candidates:
        cand = str(candidate or "").strip()
        if not cand or cand in seen_candidates:
            continue
        seen_candidates.add(cand)
        try:
            data = json.loads(cand)
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        return []

    if isinstance(data, dict) and isinstance(data.get("interview_questions"), list):
        items = data.get("interview_questions") or []
    elif isinstance(data, dict) and isinstance(data.get("questions"), list):
        items = data.get("questions") or []
    elif isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data.get("items") or []
    elif isinstance(data, dict) and isinstance(data.get("data"), list):
        items = data.get("data") or []
    elif isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = [data]
    else:
        items = []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in items:
        if not isinstance(row, dict):
            continue
        normalized = _normalize_question_item(row)
        if not normalized:
            continue
        key = re.sub(r"\s+", " ", normalized["question"]).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def _generate_questions_with_openai_from_ncs(
    ncs_matches: list[dict[str, Any]],
    ncs_ksa: list[dict[str, Any]] | None = None,
    jd_text: str = "",
    strengths: str = "",
    target_count: int = 6,
    mode: str = "diverse",
    extra_context: str = "",
) -> list[dict[str, Any]]:
    api_key = settings.openai_key()
    if not api_key:
        return []

    prompt = _build_question_generation_prompt(
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        jd_text=jd_text,
        strengths=strengths,
        mode=mode,
        target_count=target_count,
        extra_context=extra_context,
    )

    try:
        target_n = max(1, int(target_count or 1))
    except Exception:
        target_n = 1

    try:
        timeout_sec = float(str(os.getenv("OPENAI_QUESTION_TIMEOUT_SEC", "60")).strip() or "60")
    except Exception:
        timeout_sec = 60.0
    timeout_sec = max(15.0, min(240.0, timeout_sec))

    try:
        max_variants = int(str(os.getenv("OPENAI_QUESTION_VARIANT_ATTEMPTS", "3")).strip() or "3")
    except Exception:
        max_variants = 3
    max_variants = max(1, min(3, max_variants))

    payload_base = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": "怨듦났湲곌? 援ъ“??硫댁젒 ?ㅺ퀎 ?꾨Ц媛. JSON留?異쒕젰."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.5,
        "max_tokens": 4000,
    }

    attempts: list[tuple[dict[str, Any], float]] = []
    p1 = copy.deepcopy(payload_base)
    p1["response_format"] = {"type": "json_object"}
    attempts.append((p1, timeout_sec))

    p2 = copy.deepcopy(payload_base)
    attempts.append((p2, min(240.0, timeout_sec + 20.0)))

    p3 = copy.deepcopy(payload_base)
    p3["temperature"] = 0.3
    p3["messages"][1]["content"] = (
        str(p3["messages"][1]["content"])
        + "\n\n以묒슂: ?ㅻ챸臾??놁씠 JSON留?異쒕젰?섏꽭?? ?좏슚??JSON 媛앹껜 1媛쒕? 諛섑솚?섏꽭??"
    )
    attempts.append((p3, min(240.0, timeout_sec + 30.0)))

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload, req_timeout in attempts[:max_variants]:
        try:
            data = post_chat_completions_with_retries(
                payload=payload,
                api_key=api_key,
                timeout_sec=req_timeout,
            )
        except Exception:
            continue

        parsed = _parse_openai_response(_extract_message_content(data))
        if not parsed:
            continue

        for row in parsed:
            q_key = re.sub(r"\s+", " ", str((row or {}).get("question", "")).strip()).lower()
            if not q_key or q_key in seen:
                continue
            seen.add(q_key)
            merged.append(row)
            if len(merged) >= target_n:
                break
        if len(merged) >= target_n:
            break

    return merged[:target_n]

