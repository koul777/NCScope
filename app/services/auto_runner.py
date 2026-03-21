"""
Auto Runner - 자동 동기화 백그라운드 실행기
"""

from __future__ import annotations

import threading
import time

from app.settings import settings


def start_auto_runner() -> None:
    """설정에 따라 백그라운드 동기화 작업 시작."""
    if not (settings.auto_sync_public_inst() or settings.auto_sync_ncs()):
        return

    interval = settings.sync_interval_minutes() * 60

    def _run_loop() -> None:
        # 첫 실행은 시작 30초 후 (서버 완전 시작 대기)
        time.sleep(30)
        while True:
            _run_sync_tasks()
            time.sleep(interval)

    thread = threading.Thread(target=_run_loop, daemon=True, name="auto-runner")
    thread.start()


def _run_sync_tasks() -> None:
    """동기화 작업 실행."""
    if settings.auto_sync_public_inst():
        try:
            from app.services.sync_workers import sync_public_institutions
            sync_public_institutions()
        except Exception:
            pass

    if settings.auto_sync_ncs():
        try:
            from app.services.sync_workers import sync_ncs_units
            sync_ncs_units()
        except Exception:
            pass
