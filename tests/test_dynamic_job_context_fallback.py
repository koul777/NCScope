from __future__ import annotations

from app.main import (
    SUPPORTED_INTERVIEW_METHODS,
    _question_for_method,
    _task_conditions_for_method,
)


JOB_CONTEXT = (
    "[\ub2f4\ub2f9\uc5c5\ubb34]\ubd80\uc11c \uc758\uacac\uc744 \uc218\uc9d1\ud558\uace0 \uc815\ucc45 \ubcf4\uace0\uc11c\ub97c \uc791\uc131\ud558\uba70 \uc131\uacfc\uc9c0\ud45c\ub97c \uc124\uacc4\ud55c\ub2e4.\n"
    "[\uc9c1\ubb34\uae30\uc220\uc11c]\uc815\ucc45 \uc790\ub8cc\ub97c \uac80\uc99d\ud558\uace0 \ubd84\uc11d\ud55c\ub2e4.\n"
    "[\uacf5\uace0\ubb38]\uc815\ucc45 \uae30\ud68d \ubc0f \uae30\uad00 \ud611\uc5c5 \uc5c5\ubb34\n"
    "[\uba74\uc811\ud3c9\uac00\ud56d\ubaa9]\ubb38\uc81c\ud574\uacb0 \ubc0f \uc758\uc0ac\uc18c\ud1b5"
)


def test_every_method_keeps_uploaded_job_context_in_deterministic_paths() -> None:
    for method in SUPPORTED_INTERVIEW_METHODS:
        question = _question_for_method(
            method=method,
            subject="\uc815\ucc45\uae30\ud68d",
            focus="\uc815\ucc45 \uc790\ub8cc \ubd84\uc11d",
            detail="\uc815\ucc45\uae30\ud68d",
            comp_def="\uc815\ucc45 \uc790\ub8cc\ub97c \uac80\uc99d\ud558\uace0 \ubd84\uc11d\ud558\ub294 \ub2a5\ub825",
            job_context_text=JOB_CONTEXT,
        )
        conditions = _task_conditions_for_method(
            method=method,
            subject="\uc815\ucc45\uae30\ud68d",
            focus="\uc815\ucc45 \uc790\ub8cc \ubd84\uc11d",
            detail="\uc815\ucc45\uae30\ud68d",
            comp_def="\uc815\ucc45 \uc790\ub8cc\ub97c \uac80\uc99d\ud558\uace0 \ubd84\uc11d\ud558\ub294 \ub2a5\ub825",
            job_context_text=JOB_CONTEXT,
        )
        assert "\uc815\ucc45" in question
        assert "\uc804\uae30" not in question
        if method in {"\uc0c1\ud669\uba74\uc811", "\ubc1c\ud45c\uba74\uc811", "\ud1a0\ub860\uba74\uc811", "\uc778\ubc14\uc2a4\ucf13\uba74\uc811", "\ucc3d\uc758\uc801 \ubb38\uc81c\ud574\uacb0\ub825\uba74\uc811"}:
            assert "\uc815\ucc45" in str(conditions)
            assert "\uae34\uae09 \uc694\uccad 1\uac74" not in str(conditions)
