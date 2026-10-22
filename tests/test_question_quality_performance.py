"""Pin quality-check helpers whose speed was improved without changing results."""

from __future__ import annotations

import json
import re
import sys
from difflib import SequenceMatcher

from app import main
from app.services import question_evaluation_alignment as qea
from app.services import question_realism as qr

# Values computed with the code before the speed-up (commit 7ca17b3).
_PINNED = json.loads(
    r"""
{
 "patterns": [
  [
   "\ubb38\uc11c \uc791\uc131",
   "\ubb38\uc11c\uc791\uc131 \uc5c5\ubb34\uc5d0\uc11c \uacaa\uc740 \uc77c\uc744 \ub9d0\ud574 \uc8fc\uc138\uc694",
   [
    [
     "named_work_context",
     [
      [
       0,
       9
      ]
     ]
    ],
    [
     "named_assignee_role",
     []
    ],
    [
     "named_task_context",
     []
    ],
    [
     "named_label_explanation",
     []
    ],
    [
     "named_ksa_application",
     []
    ]
   ]
  ],
  [
   "\ubb38\uc11c \uc791\uc131",
   "'\ubb38\uc11c\u00b7\uc791\uc131' \ub2f4\ub2f9\uc790\ub85c\uc11c \ud310\ub2e8\ud55c \ub0b4\uc6a9\uc744 \uc4f0\uc138\uc694",
   [
    [
     "named_work_context",
     []
    ],
    [
     "named_assignee_role",
     [
      [
       0,
       13
      ]
     ]
    ],
    [
     "named_task_context",
     []
    ],
    [
     "named_label_explanation",
     []
    ],
    [
     "named_ksa_application",
     []
    ]
   ]
  ],
  [
   "\ubb38\uc11c \uc791\uc131",
   "\ubb38\uc11c \uc791\uc131\uc758 \uc758\ubbf8\ub97c \uc124\uba85\ud558\uc138\uc694",
   [
    [
     "named_work_context",
     []
    ],
    [
     "named_assignee_role",
     []
    ],
    [
     "named_task_context",
     []
    ],
    [
     "named_label_explanation",
     [
      [
       0,
       9
      ]
     ]
    ],
    [
     "named_ksa_application",
     []
    ]
   ]
  ],
  [
   "\ubb38\uc11c\uc791\uc131\ub2a5\ub825",
   "\ubb38\uc11c\uc791\uc131\ub2a5\ub825\uc744 \uc801\uc6a9\ud588\uc2b5\ub2c8\ub2e4",
   [
    [
     "named_work_context",
     []
    ],
    [
     "named_assignee_role",
     []
    ],
    [
     "named_task_context",
     []
    ],
    [
     "named_label_explanation",
     []
    ],
    [
     "named_ksa_application",
     [
      [
       0,
       11
      ]
     ]
    ],
    [
     "exact_taxonomy_label",
     [
      [
       0,
       6
      ]
     ]
    ]
   ]
  ],
  [
   "KSA\uad00\ub9ac",
   "\u212aSA\uad00\ub9ac \uc5c5\ubb34\uc5d0\uc11c \ud655\uc778\ud55c \uc810",
   [
    [
     "named_work_context",
     [
      [
       0,
       10
      ]
     ]
    ],
    [
     "named_assignee_role",
     []
    ],
    [
     "named_task_context",
     []
    ],
    [
     "named_label_explanation",
     []
    ],
    [
     "named_ksa_application",
     []
    ]
   ]
  ],
  [
   "KSA\uad00\ub9ac",
   "ksa \uad00\ub9ac \uc5c5\ubb34 \uc911 \ubb38\uc81c",
   [
    [
     "named_work_context",
     [
      [
       0,
       11
      ]
     ]
    ],
    [
     "named_assignee_role",
     []
    ],
    [
     "named_task_context",
     []
    ],
    [
     "named_label_explanation",
     []
    ],
    [
     "named_ksa_application",
     []
    ]
   ]
  ],
  [
   "KSA\uad00\ub9ac",
   "\u0130ksa\uad00\ub9ac \uc5c5\ubb34\uc5d0\uc11c",
   [
    [
     "named_work_context",
     []
    ],
    [
     "named_assignee_role",
     []
    ],
    [
     "named_task_context",
     []
    ],
    [
     "named_label_explanation",
     []
    ],
    [
     "named_ksa_application",
     []
    ]
   ]
  ],
  [
   "KSA\uad00\ub9ac",
   "x\u017fksa\uad00\ub9ac \uc5c5\ubb34\uc5d0\uc11c",
   [
    [
     "named_work_context",
     []
    ],
    [
     "named_assignee_role",
     []
    ],
    [
     "named_task_context",
     []
    ],
    [
     "named_label_explanation",
     []
    ],
    [
     "named_ksa_application",
     []
    ]
   ]
  ],
  [
   "\uc704\ud5d8\uc131 \ud3c9\uac00 \uae30\uc900",
   "\uc704\ud5d8\uc131\ud3c9\uac00\uae30\uc900\uc740 \ubb34\uc5c7\uc778\uac00\uc694, \uc704\ud5d8\uc131 \ud3c9\uac00 \uae30\uc900\uc744 \ud655\uc778\ud588\ub098\uc694",
   [
    [
     "named_work_context",
     []
    ],
    [
     "named_assignee_role",
     []
    ],
    [
     "named_task_context",
     []
    ]
   ]
  ],
  [
   "Risk Assessment",
   "RISK-ASSESSMENT \uad00\ub828 \uc5c5\ubb34\uc5d0\uc11c, risk assessment \uacfc\uc81c\ub85c",
   [
    [
     "named_work_context",
     [
      [
       0,
       23
      ]
     ]
    ],
    [
     "named_assignee_role",
     []
    ],
    [
     "named_task_context",
     [
      [
       25,
       44
      ]
     ]
    ],
    [
     "named_label_explanation",
     []
    ],
    [
     "named_ksa_application",
     []
    ]
   ]
  ]
 ],
 "families": [
  [
   "\ub2f9\uc2dc \ud310\ub2e8 \uadfc\uac70\uc640 \uc9c1\uc811 \uc218\ud589\ud55c \ud589\ub3d9, \uadf8 \uacb0\uacfc\ub97c \uc124\uba85\ud558\uc138\uc694",
   "\uacbd\ud5d8\uba74\uc811",
   [
    "decision_criteria",
    "evidence_basis",
    "judgment_action",
    "outcome_evidence"
   ]
  ],
  [
   "\uc5b4\ub5a4 \uae30\uc900\uc73c\ub85c \uc6b0\uc120\uc21c\uc704\ub97c \uc815\ud558\uace0 \ub204\uad6c\uc5d0\uac8c \ubcf4\uace0\ud560\uc9c0 \ub9d0\uc500\ud574 \uc8fc\uc138\uc694",
   "\uc0c1\ud669\uba74\uc811",
   [
    "escalation_stakeholder"
   ]
  ],
  [
   "\uc790\ub8cc\uc758 \uc0dd\uc131 \uc2dc\uc810\uacfc \ubd88\uc77c\uce58 \uc6d0\uc778\uc744 \ud655\uc778\ud55c \ubc29\ubc95",
   "\ubc1c\ud45c\uba74\uc811",
   [
    "comparison_basis",
    "data_diagnosis",
    "data_generation_time",
    "discrepancy_reconciliation",
    "document_comparison",
    "verification_method"
   ]
  ]
 ],
 "stems": [
  [
   "\ud655\uc778\ud558\uc600\uc73c\uba70",
   "\ud655\uc778\ud558\uc600\uc73c\uba70"
  ],
  [
   "\ubcf4\uace0\uc11c\uc5d0\uc11c\ub294",
   "\ubcf4\uace0\uc11c"
  ],
  [
   "\ud310\ub2e8\uc73c\ub85c\ubd80\ud130",
   "\ud310\ub2e8"
  ],
  [
   "\uad00\ub9ac",
   "\uad00\ub9ac"
  ],
  [
   "\uacb0\uacfc\ub97c",
   "\uacb0\uacfc"
  ]
 ]
}
"""
)


def test_label_like_patterns_match_the_same_spans() -> None:
    for label, text, expected in _PINNED["patterns"]:
        actual = [
            [name, [[match.start(), match.end()] for match in pattern.finditer(text)]]
            for name, pattern in qr._label_like_patterns(label)
        ]
        assert actual == expected, (label, text)
        # A repeated call returns the same compiled patterns.
        assert qr._label_like_patterns(label) is qr._label_like_patterns(label)


def test_scoped_label_classes_match_the_case_insensitive_classes() -> None:
    word = re.compile(r"[0-9A-Za-z가-힣]", re.IGNORECASE)
    non_word = re.compile(r"[^0-9A-Za-z가-힣]", re.IGNORECASE)
    scoped_word = re.compile(qr._LABEL_WORD_CHAR, re.IGNORECASE)
    scoped_non_word = re.compile(qr._LABEL_NON_WORD_CHAR, re.IGNORECASE)
    for code_point in range(sys.maxunicode + 1):
        if 0xD800 <= code_point <= 0xDFFF:
            continue
        char = chr(code_point)
        assert bool(word.fullmatch(char)) == bool(scoped_word.fullmatch(char)), hex(code_point)
        assert bool(non_word.fullmatch(char)) == bool(scoped_non_word.fullmatch(char)), hex(code_point)


def test_detect_families_and_stems_are_pinned() -> None:
    for text, method, expected in _PINNED["families"]:
        assert sorted(qea._detect_families(text, method)) == expected
        assert qea._detect_families(text, method) == qea._detect_families(text, method)
    for token, expected in _PINNED["stems"]:
        assert qea._stem_token(token) == expected


def test_anchor_similarity_ratio_equals_sequence_matcher() -> None:
    pairs = [
        ("판단 근거와 행동을 구체적으로 제시함", "판단 근거와 행동을 일부 제시함"),
        ("", "근거 없음"),
        ("같은 문장", "같은 문장"),
    ]
    for left, right in pairs:
        assert main._anchor_similarity_ratio(left, right) == SequenceMatcher(None, left, right).ratio()
