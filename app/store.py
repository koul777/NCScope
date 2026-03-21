from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock


@dataclass
class AttachmentRecord:
    id: int
    atch_type: str
    file_name: str
    content_text: str


@dataclass
class PostingRecord:
    id: int
    title: str
    institution_name: str
    region_code: str
    r1000: str
    r2000: str
    r6000: str
    r7000: str
    a2000: str
    a1000: str
    attachments: list[AttachmentRecord] = field(default_factory=list)
    requirements: list[dict] = field(default_factory=list)


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._posting_id = 1
        self._attachment_id = 1
        self.postings: dict[int, PostingRecord] = {}

    def create_posting(self, payload: dict) -> PostingRecord:
        with self._lock:
            posting_id = self._posting_id
            self._posting_id += 1
            attachments: list[AttachmentRecord] = []
            for at in payload["attachments"]:
                att = AttachmentRecord(
                    id=self._attachment_id,
                    atch_type=at["atch_type"],
                    file_name=at["file_name"],
                    content_text=at["content_text"],
                )
                self._attachment_id += 1
                attachments.append(att)

            posting = PostingRecord(
                id=posting_id,
                title=payload["title"],
                institution_name=payload["institution_name"],
                region_code=payload["region_code"],
                r1000=payload["r1000"],
                r2000=payload["r2000"],
                r6000=payload["r6000"],
                r7000=payload["r7000"],
                a2000=payload["a2000"],
                a1000=payload["a1000"],
                attachments=attachments,
            )
            self.postings[posting_id] = posting
            return posting

    def get_posting(self, posting_id: int) -> PostingRecord | None:
        return self.postings.get(posting_id)

    def list_postings(self) -> list[PostingRecord]:
        return list(self.postings.values())

