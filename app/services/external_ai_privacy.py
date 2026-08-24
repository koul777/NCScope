from __future__ import annotations

import re


_EMAIL_RE = re.compile(
    r"(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9.-])",
    re.IGNORECASE,
)
_KOREAN_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?82[- .]?)?(?:0?1[016789]|0?2|0?[3-6][1-5])"
    r"[- .]?\d{3,4}[- .]?\d{4}(?!\d)"
)
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-8]\d{6}(?!\d)")
_CONTACT_LABEL_RE = re.compile(
    r"^\s*(?:채용\s*)?(?:문의(?:처|전화|메일|이메일)?|연락처|담당자|담당자명|담당\s*부서)"
    r"\s*[:：]\s*.+$",
    re.IGNORECASE,
)
_SIGNATURE_LABEL_RE = re.compile(
    r"^\s*(?:성명|이름|서명|서명란|직인|날인)\s*[:：]\s*.*$",
    re.IGNORECASE,
)
_SIGNATURE_ONLY_RE = re.compile(
    r"^\s*(?:\(?\s*(?:서명|직인|날인)\s*\)?|(?:인|印))\s*$",
    re.IGNORECASE,
)
_LABELED_PERSON_RE = re.compile(
    r"(?P<label>(?:채용\s*)?담당자(?:명)?|성명|이름)\s*[:：|]\s*[가-힣]{2,4}"
)
_UNDELIMITED_LABELED_PERSON_RE = re.compile(
    r"(?P<label>(?:채용\s*)?담당자(?:명)?|성명|이름)\s+"
    r"[김이박최정강조윤장임한오서신권황안송류유홍전고문양손배백허남심노하곽성차주우구민진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부가복태목]"
    r"[가-힣]{1,3}(?=\s*(?:$|[(/,|]|전화|연락|문의|이메일|메일))"
)
_NAMED_SIGNATURE_RE = re.compile(
    r"^\s*[가-힣]{2,4}\s*[\(（]\s*(?:서명|인|印)\s*[\)）]\s*$"
)
_CONTACT_MARKER_RE = re.compile(r"(?:채용\s*문의|문의처|연락처|채용\s*담당)", re.IGNORECASE)


def sanitize_external_ai_source_text(value: object, *, max_chars: int | None = None) -> str:
    """Remove direct contact identifiers from text sent to an external model.

    Matching and review code may retain the original document locally.  This
    helper is intentionally applied only at the external-AI boundary so that
    official NCS classification evidence is not weakened by redaction.
    """

    text = str(value or "")
    if not text:
        return ""

    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line
        if _CONTACT_LABEL_RE.match(line) or (
            _CONTACT_MARKER_RE.search(line)
            and (_EMAIL_RE.search(line) or _KOREAN_PHONE_RE.search(line))
        ):
            cleaned_lines.append("[연락처 정보 제거]")
            continue
        if (
            _SIGNATURE_LABEL_RE.match(line)
            or _SIGNATURE_ONLY_RE.match(line)
            or _NAMED_SIGNATURE_RE.match(line)
        ):
            cleaned_lines.append("[서명 정보 제거]")
            continue
        line = _LABELED_PERSON_RE.sub(
            lambda match: f"{match.group('label')}: [이름 제거]",
            line,
        )
        line = _UNDELIMITED_LABELED_PERSON_RE.sub(
            lambda match: f"{match.group('label')}: [이름 제거]",
            line,
        )
        line = _EMAIL_RE.sub("[이메일 제거]", line)
        line = _KOREAN_PHONE_RE.sub("[전화번호 제거]", line)
        line = _RESIDENT_ID_RE.sub("[주민등록번호 제거]", line)
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines)
    if max_chars is not None:
        cleaned = cleaned[: max(0, int(max_chars))]
    return cleaned
