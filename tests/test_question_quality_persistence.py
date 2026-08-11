from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

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


def test_concurrent_reviews_leave_exactly_one_active_decision(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "concurrent-quality.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(repository, "SessionLocal", session_factory)
    question = "문서 오류를 확인한 결과와 조치를 설명해 주세요."
    run = repository.create_question_quality_run(_run_payload(question))
    barrier = threading.Barrier(2)

    def submit(verdict: str) -> dict:
        barrier.wait(timeout=5)
        return repository.record_question_quality_review(
            {
                "run_id": run["id"],
                "review_token": run["review_token"],
                "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
                "question_text": question,
                "verdict": verdict,
                "issue_codes": ["too_generic"] if verdict == "needs_edit" else [],
                "reviewer_ref": f"concurrent-{verdict}",
            }
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ("approve", "needs_edit")))

    persisted = repository.get_question_quality_run(run["id"])
    assert len(results) == 2
    assert persisted is not None
    assert len(persisted["reviews"]) == 2
    active = [review for review in persisted["reviews"] if review["active"]]
    assert len(active) == 1
    expected_decision = "approved" if active[0]["verdict"] == "approve" else "needs_edit"
    assert persisted["final_decision"] == expected_decision


def test_concurrent_review_and_rollback_preserve_a_single_consistent_decision(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "concurrent-review-rollback.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(repository, "SessionLocal", session_factory)
    question = "문서 오류 점검 결과와 후속 조치를 설명해 주세요."
    question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
    run = repository.create_question_quality_run(_run_payload(question))
    repository.record_question_quality_review(
        {
            "run_id": run["id"],
            "review_token": run["review_token"],
            "question_hash": question_hash,
            "question_text": question,
            "verdict": "approve",
            "issue_codes": [],
        }
    )
    barrier = threading.Barrier(2)

    def replace_review() -> dict:
        barrier.wait(timeout=5)
        return repository.record_question_quality_review(
            {
                "run_id": run["id"],
                "review_token": run["review_token"],
                "question_hash": question_hash,
                "question_text": question,
                "verdict": "needs_edit",
                "issue_codes": ["missing_ksa_evidence"],
            }
        )

    def roll_back_review() -> dict:
        barrier.wait(timeout=5)
        return repository.rollback_question_quality_review(
            run_id=run["id"],
            review_token=run["review_token"],
            question_hash=question_hash,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        replacement = executor.submit(replace_review)
        rollback = executor.submit(roll_back_review)
        replacement.result(timeout=10)
        rollback.result(timeout=10)

    persisted = repository.get_question_quality_run(run["id"])
    assert persisted is not None
    assert len(persisted["reviews"]) == 3
    active = [review for review in persisted["reviews"] if review["active"]]
    assert len(active) == 1
    expected_decision = "approved" if active[0]["verdict"] == "approve" else "needs_edit"
    assert persisted["final_decision"] == expected_decision
    assert sum(review["verdict"] == "rollback" for review in persisted["reviews"]) == 1


def test_review_and_feedback_survive_database_reconnection(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "restart-quality.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )
    Base.metadata.create_all(engine)
    first_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(repository, "SessionLocal", first_factory)
    question = "문서 검증 절차와 산출물 확인 결과를 설명해 주세요."
    run = repository.create_question_quality_run(_run_payload(question))
    repository.record_question_quality_review(
        {
            "run_id": run["id"],
            "review_token": run["review_token"],
            "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
            "question_text": question,
            "verdict": "needs_edit",
            "issue_codes": ["missing_ksa_evidence"],
        }
    )
    engine.dispose()

    restarted_engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )
    restarted_factory = sessionmaker(
        bind=restarted_engine,
        autoflush=False,
        autocommit=False,
        future=True,
    )
    monkeypatch.setattr(repository, "SessionLocal", restarted_factory)

    assert repository.verify_question_quality_run_token(run["id"], run["review_token"]) is True
    persisted = repository.get_question_quality_run(run["id"])
    feedback = repository.list_question_quality_feedback(["0202030201_25v3"])
    assert persisted is not None
    assert persisted["final_decision"] == "needs_edit"
    assert feedback[0]["question_text"] == question
    assert feedback[0]["issue_codes"] == ["missing_ksa_evidence"]
    restarted_engine.dispose()


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
    assert control["review_token"].startswith("qqt_")
    assert len(control["review_token"]) == 52
    assert strategy["interview_questions"][0]["question_hash"]
    assert "문서 요구사항" not in str(captured["evidence"])
    assert captured["evidence"]["question_items"][0]["question_hash"]


def test_register_quality_evidence_degrades_safely_when_store_is_unavailable(monkeypatch) -> None:
    def fail_create(_payload):
        raise RuntimeError("simulated quality evidence store outage")

    monkeypatch.setattr(main, "create_question_quality_run", fail_create)
    strategy = {
        "interview_questions": [
            {
                "question": "문서 오류의 원자료와 검토 이력을 대조한 절차와 결과를 설명해 주세요.",
                "type": "경험면접",
                "ncsClCd": "0202030201_25v3",
            }
        ],
        "question_quality_report": {
            "policy": "policy-v1",
            "passed": True,
            "summary": {"question_count": 1, "ready_count": 1, "needs_review_count": 0},
            "items": [{"index": 1, "ready": True, "issues": [], "checks": {}}],
        },
    }

    control = main._register_question_quality_evidence(
        strategy,
        source_endpoint="test",
        ncs_matches=[{"ncsClCd": "0202030201_25v3", "compeUnitName": "문서작성"}],
    )

    assert strategy["interview_questions"][0]["question"]
    assert strategy["interview_questions"][0]["question_hash"]
    assert control["evidence_recorded"] is False
    assert control["run_id"] == ""
    assert control["review_token"] == ""
    assert control["review_required"] is True
    assert control["escalation_required"] is True
    assert "quality_evidence_store_unavailable" in control["trigger_codes"]
    assert "simulated quality evidence store outage" not in str(control)


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


def test_rollback_endpoint_returns_conflict_when_active_review_changed(monkeypatch) -> None:
    def fake_rollback(**kwargs):
        assert kwargs["expected_review_id"] == 73
        raise repository.QuestionQualityReviewConflictError("the active review changed")

    monkeypatch.setattr(main, "rollback_question_quality_review", fake_rollback)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/quality/runs/run-1/questions/hash/rollback-review",
            json={"review_token": "token", "expected_review_id": 73},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "the active review changed"


def test_review_endpoint_returns_conflict_when_active_review_changed(monkeypatch) -> None:
    def fake_record(payload):
        assert payload["expected_review_id"] == 0
        raise repository.QuestionQualityReviewConflictError("the active review changed")

    monkeypatch.setattr(main, "record_question_quality_review", fake_record)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/quality/runs/run-1/review",
            json={
                "review_token": "token",
                "question_hash": "hash",
                "verdict": "approve",
                "issue_codes": [],
                "expected_review_id": 0,
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "the active review changed"


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

    first_review = repository.record_question_quality_review(review_payload)
    repeated = repository.record_question_quality_review(review_payload)

    assert repeated["run_decision"] == "reviewing"
    assert repeated["id"] == first_review["id"]
    assert repeated["idempotent"] is True
    persisted = repository.get_question_quality_run(run["id"])
    assert persisted is not None
    assert len(persisted["reviews"]) == 1


def test_review_retry_with_changed_note_or_reviewer_remains_auditable(isolated_quality_db) -> None:
    question = "문서 오류를 확인한 결과와 조치를 설명해 주세요."
    run = repository.create_question_quality_run(_run_payload(question))
    base = {
        "run_id": run["id"],
        "review_token": run["review_token"],
        "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "question_text": question,
        "verdict": "needs_edit",
        "issue_codes": ["missing_ksa_evidence"],
    }

    first = repository.record_question_quality_review({**base, "reviewer_ref": "reviewer-a"})
    changed_note = repository.record_question_quality_review(
        {**base, "reviewer_ref": "reviewer-a", "note": "산출물 확인 질문을 추가하세요."}
    )
    changed_reviewer = repository.record_question_quality_review(
        {**base, "reviewer_ref": "reviewer-b", "note": "산출물 확인 질문을 추가하세요."}
    )

    assert first["idempotent"] is False
    assert changed_note["idempotent"] is False
    assert changed_reviewer["idempotent"] is False
    assert len({first["id"], changed_note["id"], changed_reviewer["id"]}) == 3
    persisted = repository.get_question_quality_run(run["id"])
    assert persisted is not None
    assert len(persisted["reviews"]) == 3
    assert len([review for review in persisted["reviews"] if review["active"]]) == 1


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


def test_exact_rollback_retry_is_idempotent_and_does_not_step_back_twice(isolated_quality_db) -> None:
    question = "문서 오류를 확인한 뒤 수정 이력과 승인 결과를 설명해 주세요."
    run = repository.create_question_quality_run(_run_payload(question))
    base = {
        "run_id": run["id"],
        "review_token": run["review_token"],
        "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "question_text": question,
    }
    approved = repository.record_question_quality_review(
        {**base, "verdict": "approve", "issue_codes": [], "reviewer_ref": "reviewer-a"}
    )
    edited = repository.record_question_quality_review(
        {
            **base,
            "verdict": "needs_edit",
            "issue_codes": ["too_generic"],
            "reviewer_ref": "reviewer-a",
        }
    )
    rollback_args = {
        "run_id": run["id"],
        "review_token": run["review_token"],
        "question_hash": base["question_hash"],
        "reviewer_ref": "reviewer-a",
        "note": "응답 유실 뒤 동일 롤백 재전송",
        "expected_review_id": edited["id"],
    }

    first = repository.rollback_question_quality_review(**rollback_args)
    repeated = repository.rollback_question_quality_review(**rollback_args)

    assert first["idempotent"] is False
    assert repeated["idempotent"] is True
    assert repeated["rollback_event_id"] == first["rollback_event_id"]
    assert repeated["rolled_back_review_id"] == first["rolled_back_review_id"]
    assert repeated["restored_review_id"] == approved["id"]
    persisted = repository.get_question_quality_run(run["id"])
    assert persisted is not None
    assert len(persisted["reviews"]) == 3
    active = [review for review in persisted["reviews"] if review["active"]]
    assert len(active) == 1
    assert active[0]["id"] == approved["id"]
    assert persisted["final_decision"] == "approved"


def test_rollback_after_prior_rollback_restores_actual_predecessor_not_id_neighbor(isolated_quality_db) -> None:
    question = "문서 검증 결과를 기록하고 예외 승인까지 처리한 과정을 설명해 주세요."
    run = repository.create_question_quality_run(_run_payload(question))
    base = {
        "run_id": run["id"],
        "review_token": run["review_token"],
        "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "question_text": question,
        "reviewer_ref": "reviewer-a",
    }
    approved = repository.record_question_quality_review(
        {**base, "verdict": "approve", "issue_codes": []}
    )
    edited = repository.record_question_quality_review(
        {**base, "verdict": "needs_edit", "issue_codes": ["too_generic"]}
    )
    first_rollback = repository.rollback_question_quality_review(
        run_id=run["id"],
        review_token=run["review_token"],
        question_hash=base["question_hash"],
        reviewer_ref="reviewer-a",
        note="수정필요 결정을 롤백",
        expected_review_id=edited["id"],
    )
    rejected = repository.record_question_quality_review(
        {**base, "verdict": "reject", "issue_codes": ["wrong_ncs_alignment"]}
    )

    second_rollback = repository.rollback_question_quality_review(
        run_id=run["id"],
        review_token=run["review_token"],
        question_hash=base["question_hash"],
        reviewer_ref="reviewer-a",
        note="거절 결정을 롤백",
        expected_review_id=rejected["id"],
    )

    assert first_rollback["rolled_back_review_id"] == edited["id"]
    assert first_rollback["restored_review_id"] == approved["id"]
    assert second_rollback["rolled_back_review_id"] == rejected["id"]
    assert second_rollback["restored_review_id"] == approved["id"]
    persisted = repository.get_question_quality_run(run["id"])
    assert persisted is not None
    active = [review for review in persisted["reviews"] if review["active"]]
    assert len(active) == 1
    assert active[0]["id"] == approved["id"]
    assert persisted["final_decision"] == "approved"


def test_stale_rollback_retry_cannot_undo_a_newer_review(isolated_quality_db) -> None:
    question = "규정 위반 징후를 분류하고 조치 우선순위를 정하는 과정을 설명해 주세요."
    run = repository.create_question_quality_run(_run_payload(question))
    base = {
        "run_id": run["id"],
        "review_token": run["review_token"],
        "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "question_text": question,
        "reviewer_ref": "reviewer-a",
    }
    repository.record_question_quality_review(
        {**base, "verdict": "approve", "issue_codes": []}
    )
    edited = repository.record_question_quality_review(
        {**base, "verdict": "needs_edit", "issue_codes": ["too_generic"]}
    )
    rollback_args = {
        "run_id": run["id"],
        "review_token": run["review_token"],
        "question_hash": base["question_hash"],
        "reviewer_ref": "reviewer-a",
        "note": "지연 재전송 충돌 검증",
        "expected_review_id": edited["id"],
    }
    repository.rollback_question_quality_review(**rollback_args)
    rejected = repository.record_question_quality_review(
        {**base, "verdict": "reject", "issue_codes": ["wrong_ncs_alignment"]}
    )

    with pytest.raises(repository.QuestionQualityReviewConflictError, match="active review changed"):
        repository.rollback_question_quality_review(**rollback_args)

    persisted = repository.get_question_quality_run(run["id"])
    assert persisted is not None
    active = [review for review in persisted["reviews"] if review["active"]]
    assert len(active) == 1
    assert active[0]["id"] == rejected["id"]
    assert persisted["final_decision"] == "rejected"


def test_stale_review_retry_cannot_overwrite_a_newer_review(isolated_quality_db) -> None:
    question = "원자료 오류를 판별하고 검증 결과를 기록하는 과정을 설명해 주세요."
    run = repository.create_question_quality_run(_run_payload(question))
    base = {
        "run_id": run["id"],
        "review_token": run["review_token"],
        "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "question_text": question,
        "reviewer_ref": "reviewer-a",
    }
    first_payload = {
        **base,
        "verdict": "approve",
        "issue_codes": [],
        "expected_review_id": 0,
    }
    approved = repository.record_question_quality_review(first_payload)
    assert repository.record_question_quality_review(first_payload)["idempotent"] is True
    edited = repository.record_question_quality_review(
        {
            **base,
            "verdict": "needs_edit",
            "issue_codes": ["too_generic"],
            "expected_review_id": approved["id"],
        }
    )

    with pytest.raises(repository.QuestionQualityReviewConflictError, match="active review changed"):
        repository.record_question_quality_review(first_payload)

    persisted = repository.get_question_quality_run(run["id"])
    assert persisted is not None
    active = [review for review in persisted["reviews"] if review["active"]]
    assert len(active) == 1
    assert active[0]["id"] == edited["id"]
    assert persisted["final_decision"] == "needs_edit"
