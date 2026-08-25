"""Shared deterministic posting-component split contract for NCS evaluation."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence


HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SPLIT_KEY = "connected_component(document_sha256,posting_id)"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def deterministic_split(group_sha256: str, *, holdout_modulus: int) -> str:
    if not HEX64_RE.fullmatch(group_sha256) or holdout_modulus < 2:
        raise ValueError("invalid split group digest or holdout modulus")
    return (
        "gold_holdout"
        if int(group_sha256[:16], 16) % holdout_modulus == 0
        else "gold_validation"
    )


def compute_split_groups(
    records: Sequence[Mapping[str, Any]],
    *,
    holdout_modulus: int,
) -> dict[str, dict[str, str]]:
    """Recompute connected components solely from sealed document/posting IDs."""

    if holdout_modulus < 2:
        raise ValueError("holdout_modulus must be at least 2")
    documents: list[str] = []
    postings_by_index: list[list[str]] = []
    for record in records:
        digest = str(record.get("document_sha256") or "").strip().lower()
        if not HEX64_RE.fullmatch(digest):
            raise ValueError("invalid document_sha256 in split records")
        raw_postings = record.get("posting_ids")
        if not isinstance(raw_postings, list):
            raise ValueError("posting_ids must be a list")
        posting_ids = [str(value or "").strip() for value in raw_postings]
        if any(not value for value in posting_ids) or len(posting_ids) != len(
            set(posting_ids)
        ):
            raise ValueError("posting_ids are blank or duplicated")
        documents.append(digest)
        postings_by_index.append(sorted(posting_ids))
    if len(documents) != len(set(documents)):
        raise ValueError("duplicate document_sha256 in split records")

    parents = list(range(len(records)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    posting_owner: dict[str, int] = {}
    for index, posting_ids in enumerate(postings_by_index):
        for posting_id in posting_ids:
            union(index, posting_owner.setdefault(posting_id, index))

    components: dict[int, list[int]] = {}
    for index in range(len(records)):
        components.setdefault(find(index), []).append(index)

    output: dict[str, dict[str, str]] = {}
    for indexes in components.values():
        component_documents = sorted(documents[index] for index in indexes)
        component_postings = sorted(
            {
                posting_id
                for index in indexes
                for posting_id in postings_by_index[index]
            }
        )
        group_sha256 = hashlib.sha256(
            canonical_json_bytes(
                {
                    "document_sha256": component_documents,
                    "posting_ids": component_postings,
                }
            )
        ).hexdigest()
        split = deterministic_split(group_sha256, holdout_modulus=holdout_modulus)
        for index in indexes:
            output[documents[index]] = {
                "split_group_sha256": group_sha256,
                "split": split,
            }
    return output
