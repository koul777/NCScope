from scripts.check_question_quality_eval_cases import validate_cases


def test_negative_eval_case_must_enter_feedback_loop() -> None:
    case = {
        "id": 1,
        "case_type": "regression",
        "expected_decision": "fail",
        "verdict": "needs_edit",
        "question_text": "일반적인 경험을 말해 주세요.",
        "ncs_code": "0202030201_25v3",
        "issue_codes": ["too_generic"],
    }

    assert validate_cases([case]) == []


def test_eval_case_contract_rejects_missing_evidence() -> None:
    failures = validate_cases(
        [
            {
                "id": 2,
                "case_type": "negative",
                "expected_decision": "fail",
                "verdict": "reject",
                "question_text": "질문",
                "issue_codes": [],
            }
        ]
    )

    assert failures == [{"id": 2, "reason": "negative_review_contract_mismatch"}]


def test_golden_case_requires_approved_review() -> None:
    failures = validate_cases(
        [
            {
                "id": 3,
                "case_type": "golden",
                "expected_decision": "pass",
                "verdict": "needs_edit",
                "question_text": "좋은 질문",
                "issue_codes": ["excellent_golden_candidate"],
            }
        ]
    )

    assert failures == [{"id": 3, "reason": "golden_review_contract_mismatch"}]
