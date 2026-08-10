from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main
import app.repository as repository
from app.db import Base
from app.init_db import _migrate_sqlite_question_quality_schema


@pytest.fixture
def isolated_quality_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(repository, "SessionLocal", session_factory)
    return session_factory


def test_sqlite_quality_schema_migration_adds_active_to_existing_reviews() -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool, future=True)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE question_quality_reviews ("
            "id INTEGER PRIMARY KEY, verdict VARCHAR(32) NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO question_quality_reviews (id, verdict) VALUES (1, 'approve')"
        )

    _migrate_sqlite_question_quality_schema(engine)
    _migrate_sqlite_question_quality_schema(engine)

    with engine.connect() as connection:
        columns = {
            row[1]: row
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(question_quality_reviews)"
            ).all()
        }
        active = connection.exec_driver_sql(
            "SELECT active FROM question_quality_reviews WHERE id = 1"
        ).scalar_one()

    assert "active" in columns
    assert columns["active"][3] == 1
    assert active == 1


def _run_payload(question: str = "문서 오류를 확인한 경험을 설명해 주세요.") -> dict:
    question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
    return {
        "id": "qqr-test",
        "review_token": "run-scoped-secret",
        "source_endpoint": "/api/questions/generate-from-text",
        "ncs_codes": ["0202030201_25v3"],
        "competency_names": ["문서작성"],
        "quality_policy_version": "policy-v1",
        "generator_version": "generator-v1",
        "question_count": 1,
        "ready_count": 1,
        "review_required": True,
        "escalation_required": False,
        "exception_allowed": True,
        "trigger_codes": ["template_fallback"],
        "evidence": {
            "question_items": [
                {
                    "index": 1,
                    "question_hash": question_hash,
                    "ncs_code": "0202030201_25v3",
                    "method": "경험면접",
                }
            ]
        },
    }


def test_quality_review_persists_and_becomes_next_generation_feedback(isolated_quality_db) -> None:
    question = "문서 오류를 확인한 경험을 설명해 주세요."
    run = repository.create_question_quality_run(_run_payload(question))
    assert repository.verify_question_quality_run_token(run["id"], run["review_token"]) is True
    assert repository.verify_question_quality_run_token(run["id"], "wrong") is False

    review = repository.record_question_quality_review(
        {
            "run_id": run["id"],
            "review_token": run["review_token"],
            "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
            "question_text": question,
            "question_index": 1,
            "ncs_code": "0202030201_25v3",
            "method": "경험면접",
            "verdict": "needs_edit",
            "issue_codes": ["too_generic"],
            "reviewer_ref": "reviewer-a",
        }
    )

    assert review["run_decision"] == "needs_edit"
    feedback = repository.list_question_quality_feedback(["0202030201_25v3"])
    assert feedback[0]["question_text"] == question
    assert feedback[0]["issue_codes"] == ["too_generic"]
    metrics = repository.question_quality_metrics()
    assert metrics["runs"] == 1
    assert metrics["reviews"] == 1
    assert metrics["decisions"] == {"needs_edit": 1}


def test_quality_review_rejects_wrong_token_hash_and_text(isolated_quality_db) -> None:
    question = "문서 오류를 확인한 경험을 설명해 주세요."
    run = repository.create_question_quality_run(_run_payload(question))
    base = {
        "run_id": run["id"],
        "review_token": run["review_token"],
        "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "question_text": question,
        "verdict": "reject",
        "issue_codes": ["too_generic"],
    }

    with pytest.raises(PermissionError):
        repository.record_question_quality_review({**base, "review_token": "wrong"})
    with pytest.raises(ValueError, match="does not match"):
        repository.record_question_quality_review({**base, "question_text": "다른 질문"})
    with pytest.raises(ValueError, match="not part"):
        repository.record_question_quality_review({**base, "question_hash": "0" * 64, "question_text": ""})


def test_negative_review_can_be_promoted_to_regression_case(isolated_quality_db) -> None:
    question = "일반적인 경험을 말해 주세요."
    run = repository.create_question_quality_run(_run_payload(question))
    review = repository.record_question_quality_review(
        {
            "run_id": run["id"],
            "review_token": run["review_token"],
            "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
            "question_text": question,
            "verdict": "reject",
            "issue_codes": ["too_generic"],
        }
    )

    case = repository.promote_question_quality_eval_case(review["id"], "regression", "policy-v1")

    assert case["expected_decision"] == "fail"
    assert repository.list_question_quality_eval_cases() == [
        {
            "id": case["id"],
            "source_review_id": review["id"],
            "case_type": "regression",
            "quality_policy_version": "policy-v1",
            "expected_decision": "fail",
            "active": True,
            "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
            "question_text": question,
            "ncs_code": "0202030201_25v3",
            "method": "경험면접",
            "verdict": "reject",
            "issue_codes": ["too_generic"],
        }
    ]


def test_register_quality_evidence_stores_hashes_not_raw_questions(monkeypatch) -> None:
    captured = {}

    def fake_create(payload):
        captured.update(payload)
        return {"id": payload["id"], "review_token": payload["review_token"]}

    monkeypatch.setattr(main, "create_question_quality_run", fake_create)
    strategy = {
        "interview_questions": [
            {
                "question": "문서 요구사항을 확인한 경험을 설명해 주세요.",
                "type": "경험면접",
                "ncsClCd": "0202030201_25v3",
            }
        ],
        "question_quality_report": {
            "policy": "policy-v1",
            "passed": True,
            "summary": {"question_count": 1, "ready_count": 1, "needs_review_count": 0},
            "items": [{"index": 1, "ready": True, "issues": [], "checks": {"blind_hiring_safe": True}}],
        },
    }

    control = main._register_question_quality_evidence(
        strategy,
        source_endpoint="test",
        ncs_matches=[{"ncsClCd": "0202030201_25v3", "compeUnitName": "문서작성"}],
    )

    assert control["evidence_recorded"] is True
    assert strategy["interview_questions"][0]["question_hash"]
    assert "문서 요구사항" not in str(captured["evidence"])
    assert captured["evidence"]["question_items"][0]["question_hash"]


def test_review_endpoint_requires_issue_and_passes_sanitized_feedback(monkeypatch) -> None:
    captured = {}

    def fake_record(payload):
        captured.update(payload)
        return {"id": 7, "run_decision": "needs_edit"}

    monkeypatch.setattr(main, "record_question_quality_review", fake_record)
    with TestClient(main.app) as client:
        invalid = client.post(
            "/api/quality/runs/run-1/review",
            json={
                "review_token": "token",
                "question_hash": "hash",
                "question_text": "질문",
                "verdict": "needs_edit",
                "issue_codes": [],
            },
        )
        valid = client.post(
            "/api/quality/runs/run-1/review",
            json={
                "review_token": "token",
                "question_hash": "hash",
                "question_text": "질문",
                "question_index": 1,
                "verdict": "needs_edit",
                "issue_codes": ["too_generic"],
            },
        )

    assert invalid.status_code == 422
    assert valid.status_code == 200
    assert captured["run_id"] == "run-1"
    assert captured["issue_codes"] == ["too_generic"]


def test_quality_run_detail_requires_run_scoped_header(monkeypatch) -> None:
    monkeypatch.setattr(main, "verify_question_quality_run_token", lambda run_id, token: token == "valid")
    monkeypatch.setattr(main, "get_question_quality_run", lambda run_id: {"id": run_id, "reviews": []})

    with TestClient(main.app) as client:
        denied = client.get("/api/quality/runs/run-1")
        allowed = client.get("/api/quality/runs/run-1", headers={"X-Review-Token": "valid"})

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["data"]["id"] == "run-1"


def test_persistence_uses_server_evidence_metadata_not_client_claims(isolated_quality_db) -> None:
    question = "문서 오류를 확인한 경험을 설명해 주세요."
    run = repository.create_question_quality_run(_run_payload(question))

    repository.record_question_quality_review(
        {
            "run_id": run["id"],
            "review_token": run["review_token"],
            "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
            "question_text": question,
            "question_index": 99,
            "ncs_code": "forged-code",
            "method": "forged-method",
            "verdict": "needs_edit",
            "issue_codes": ["wrong_ncs_alignment"],
        }
    )

    feedback = repository.list_question_quality_feedback(["0202030201_25v3"])
    assert feedback[0]["ncs_code"] == "0202030201_25v3"
    assert feedback[0]["method"] == "경험면접"


def test_repeated_approval_of_one_question_does_not_approve_the_whole_run(isolated_quality_db) -> None:
    first = "첫 번째 질문"
    second = "두 번째 질문"
    payload = _run_payload(first)
    payload["question_count"] = 2
    payload["evidence"]["question_items"].append(
        {
            "index": 2,
            "question_hash": hashlib.sha256(second.encode("utf-8")).hexdigest(),
            "ncs_code": "0202030201_25v3",
            "method": "상황면접",
        }
    )
    run = repository.create_question_quality_run(payload)
    review_payload = {
        "run_id": run["id"],
        "review_token": run["review_token"],
        "question_hash": hashlib.sha256(first.encode("utf-8")).hexdigest(),
        "question_text": first,
        "verdict": "approve",
        "issue_codes": [],
    }

    repository.record_question_quality_review(review_payload)
    repeated = repository.record_question_quality_review(review_payload)

    assert repeated["run_decision"] == "reviewing"


def test_review_rollback_restores_previous_decision_and_removes_negative_feedback(isolated_quality_db) -> None:
    question = "문서 오류를 확인한 경험을 설명해 주세요."
    run = repository.create_question_quality_run(_run_payload(question))
    base = {
        "run_id": run["id"],
        "review_token": run["review_token"],
        "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "question_text": question,
    }
    approved = repository.record_question_quality_review({**base, "verdict": "approve", "issue_codes": []})
    edited = repository.record_question_quality_review(
        {**base, "verdict": "needs_edit", "issue_codes": ["too_generic"]}
    )

    rolled_back = repository.rollback_question_quality_review(
        run_id=run["id"],
        review_token=run["review_token"],
        question_hash=base["question_hash"],
    )

    assert approved["run_decision"] == "approved"
    assert edited["run_decision"] == "needs_edit"
    assert rolled_back["restored_review_id"] == approved["id"]
    assert rolled_back["run_decision"] == "approved"
    assert repository.list_question_quality_feedback(["0202030201_25v3"]) == []
    metrics = repository.question_quality_metrics()
    assert metrics["reviews"] == 3
    assert metrics["active_reviews"] == 1
    assert metrics["rollback_events"] == 1
