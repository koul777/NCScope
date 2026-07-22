from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.repository as repository
import app.services.sync_workers as sync_workers
from app.db import Base
from app.models import NcsUnit


def _unit(code: str, name: str) -> dict[str, str]:
    return {
        "ncsClCd": code,
        "compeUnitName": name,
        "compeUnitLevel": "4",
        "ncsLclasCdnm": "",
        "ncsMclasCdnm": "",
        "ncsSclasCdnm": "",
        "ncsSubdCdnm": "",
        "compeUnitDef": "",
    }


def test_upsert_ncs_units_keeps_all_synced_pages_active(monkeypatch):
    db_path = Path(".tmp") / f"ncs_activation_{uuid.uuid4().hex}.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(repository, "SessionLocal", testing_session)

    repository.upsert_ncs_units(
        version_tag="v1",
        units=[_unit("A1", "Unit A1"), _unit("A2", "Unit A2")],
        deactivate_existing=True,
    )
    repository.upsert_ncs_units(
        version_tag="v1",
        units=[_unit("B1", "Unit B1")],
        deactivate_existing=False,
    )

    try:
        with repository.db_session() as s:
            rows = s.execute(
                select(NcsUnit.ncs_cl_cd, NcsUnit.is_active).order_by(NcsUnit.ncs_cl_cd)
            ).all()

        assert rows == [("A1", True), ("A2", True), ("B1", True)]
    finally:
        engine.dispose()
        if db_path.exists():
            db_path.unlink()


def test_sync_ncs_units_resets_active_only_on_first_page(mocker):
    mocker.patch("app.services.sync_workers.start_ncs_sync", return_value=1)
    mocker.patch("app.services.sync_workers.finish_ncs_sync", return_value=None)
    mocker.patch(
        "app.services.sync_workers.fetch_ncs",
        return_value={"status_code": 200, "content_type": "application/json", "data": "{}"},
    )
    mocker.patch(
        "app.services.sync_workers._parse_ncs_items",
        side_effect=[
            ([_unit("A1", "Unit A1"), _unit("A2", "Unit A2")], 4),
            ([_unit("B1", "Unit B1"), _unit("B2", "Unit B2")], 4),
            ([], 4),
        ],
    )
    upsert_spy = mocker.patch("app.services.sync_workers.upsert_ncs_units", return_value=0)

    result = sync_workers.sync_ncs_units(path="Ncs1info/ncsinfo.do", pages=3, num_of_rows=2)

    assert result["upserted"] == 4
    assert upsert_spy.call_count == 2
    assert upsert_spy.call_args_list[0].kwargs["deactivate_existing"] is True
    assert upsert_spy.call_args_list[1].kwargs["deactivate_existing"] is False
