"""Shared pytest fixtures and configuration for all tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))


@pytest.fixture
def mocker():
    """Minimal pytest-mock compatible fixture for this test suite.

    Supports `mocker.patch(...)` and exposes `Mock/MagicMock` used by tests.
    This removes hard dependency on pytest-mock plugin availability.
    """

    patchers = []

    class _Mocker:
        Mock = MagicMock
        MagicMock = MagicMock

        def patch(self, target, *args, **kwargs):
            p = patch(target, *args, **kwargs)
            patchers.append(p)
            return p.start()

    m = _Mocker()
    try:
        yield m
    finally:
        for p in reversed(patchers):
            p.stop()


@pytest.fixture
def sample_jd_text():
    """Sample job description text for testing."""
    return """
    직무명: 사무행정직
    소분류: 사무행정

    주요 업무:
    - 회의 운영 및 문서 관리
    - 행정 지원 및 자료 정리
    - 사무 지원
    """


@pytest.fixture
def sample_notice_text():
    """Sample notice text for testing."""
    return """
    공공기관 사무행정 채용 공고
    기관명: 테스트 기관
    지역: 서울
    요구사항: 사무행정 경험 필수
    """


@pytest.fixture
def sample_requirements():
    """Sample extracted requirements."""
    return [
        {"item": "사무행정 업무 경험", "source": "jd", "weight": 0.9},
        {"item": "문서 관리 능력", "source": "jd", "weight": 0.8},
        {"item": "행정 지원 경험", "source": "jd", "weight": 0.7},
    ]


@pytest.fixture
def sample_profile_text():
    """Sample profile/resume text for testing."""
    return """
    3년간 사무행정 업무 담당
    문서 관리 및 회의 운영 경험
    행정 지원팀에서 근무
    """


@pytest.fixture
def sample_ncs_items():
    """Sample NCS items for testing."""
    return [
        {
            "ncsClCd": "02020302",
            "compeUnitName": "사무행정",
            "compeUnitLevel": 4,
            "ncsLclasCdnm": "경영・사무",
            "ncsMclasCdnm": "사무",
            "ncsSclasCdnm": "사무행정",
            "ncsSubdCdnm": "사무행정",
            "compeUnitDef": "직무수행에 필요한 기본적인 사무업무를 처리할 수 있는 능력",
        },
        {
            "ncsClCd": "02030201",
            "compeUnitName": "회계처리",
            "compeUnitLevel": 4,
            "ncsLclasCdnm": "경영・사무",
            "ncsMclasCdnm": "사무",
            "ncsSclasCdnm": "회계",
            "ncsSubdCdnm": "회계처리",
            "compeUnitDef": "회계전표 및 자료를 정리ㆍ정산할 수 있는 능력",
        },
    ]


@pytest.fixture
def sample_korean_text():
    """Sample text with Korean characters for encoding tests."""
    return "한글 텍스트입니다. 사무행정, 회계, 총무 등의 업무를 담당합니다."
