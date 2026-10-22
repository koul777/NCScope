from __future__ import annotations

import copy

from app import main


def _question() -> dict:
    return {
        "question": "충돌한 경영진 일정을 조정한 실제 경험과 판단 근거를 말씀해 주세요.",
        "type": "경험면접",
        "ncsClCd": "0202030103_22v2",
        "competency": "경영진 일정관리",
        "follow_ups": ["확인한 정보의 출처는 무엇입니까?"],
        "structured_follow_ups": [
            {
                "id": "FU1",
                "trigger": "priority_basis_missing",
                "answer_slot": "우선순위 근거",
                "prompt": "확인한 정보와 출처를 말씀해 주세요.",
                "evidence_target": ["정보 확인", "우선순위 판단"],
                "ask_limit": 1,
            }
        ],
        "evaluation_points": ["일정 제약을 복수 자료로 확인했는가"],
        "task_conditions": {"time_minutes": 7, "materials": ["일정표"]},
        "assessment_guide": {
            "dimensions": ["사실 확인"],
            "rating_levels": [{"score": 5, "anchor": "복수 자료를 교차 확인"}],
        },
        "ncs_traceability": {
            "question_evidence_id": "ksa_1",
            "performance_criteria": ["일정의 우선순위를 판단한다."],
        },
        "ksa_refs": ["ksa_1"],
        "ksa_evidence": [
            {
                "evidence_id": "ksa_1",
                "factorName": "일정 우선순위 판단",
                "performanceCriteria": ["일정의 우선순위를 판단한다."],
            }
        ],
        "evidence_ids": ["ksa_1"],
        "question_evidence_id": "ksa_1",
        "question_source": "openai_api",
    }


def _bundle() -> dict:
    return {
        "status": "exact_complete",
        "eligible_for_generation": True,
        "matcher_version": "ncs-exact-complete-v1",
        "fingerprint": "a" * 64,
        "details": [
            {
                "officialDetailCode": "02020301",
                "officialDetailName": "비서",
                "detailExpectedUnitBaseCount": 11,
                "detailVerifiedUnitBaseCount": 11,
                "detailRetrievalComplete": True,
                "detailRetrievalCapLimited": False,
            }
        ],
    }


def _matches() -> list[dict]:
    return [
        {
            "ncsClCd": "0202030103_22v2",
            "officialUnitBaseCode": "0202030103",
            "officialUnitName": "경영진 일정관리",
            "officialDetailCode": "02020301",
            "officialDetailName": "비서",
            "unitCatalogVerified": True,
            "detailPathCodeVerified": True,
            "unitResolutionKind": "catalog_full_code_exact",
        }
    ]


def test_question_artifact_hash_changes_for_every_material_interviewer_field() -> None:
    baseline, artifact = main._question_artifact_hash(
        _question(), ncs_matches=_matches(), ncs_match_bundle=_bundle()
    )
    assert artifact["match_lock"]["bundle_fingerprint"] == "a" * 64
    assert artifact["unit_locks"][0]["base_code"] == "0202030103"

    mutations = [
        ("question", "다른 주질문"),
        ("follow_ups", ["다른 꼬리질문"]),
        (
            "structured_follow_ups",
            [{"id": "FU1", "trigger": "always", "prompt": "다른 질문"}],
        ),
        ("evaluation_points", ["다른 평가포인트"]),
        ("task_conditions", {"time_minutes": 10}),
        ("assessment_guide", {"dimensions": ["다른 차원"]}),
        ("ncs_traceability", {"question_evidence_id": "ksa_2"}),
        ("ksa_refs", ["ksa_2"]),
        ("ksa_evidence", [{"evidence_id": "ksa_2"}]),
        ("evidence_ids", ["ksa_2"]),
    ]
    for field, value in mutations:
        changed = copy.deepcopy(_question())
        changed[field] = value
        digest, _ = main._question_artifact_hash(
            changed, ncs_matches=_matches(), ncs_match_bundle=_bundle()
        )
        assert digest != baseline, field

    changed_bundle = _bundle()
    changed_bundle["fingerprint"] = "b" * 64
    digest, _ = main._question_artifact_hash(
        _question(), ncs_matches=_matches(), ncs_match_bundle=changed_bundle
    )
    assert digest != baseline

    changed_matches = _matches()
    changed_matches[0]["unitCatalogVerified"] = False
    digest, _ = main._question_artifact_hash(
        _question(), ncs_matches=changed_matches, ncs_match_bundle=_bundle()
    )
    assert digest != baseline


def test_question_artifact_hash_is_stable_for_key_order() -> None:
    question = _question()
    reordered = {key: question[key] for key in reversed(list(question))}
    first, _ = main._question_artifact_hash(
        question, ncs_matches=_matches(), ncs_match_bundle=_bundle()
    )
    second, _ = main._question_artifact_hash(
        reordered, ncs_matches=_matches(), ncs_match_bundle=_bundle()
    )
    assert first == second
