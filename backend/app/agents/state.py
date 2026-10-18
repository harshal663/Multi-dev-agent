from __future__ import annotations
from typing import TypedDict, Literal, Annotated
import operator


class FileDict(TypedDict):
    path: str
    language: str
    content: str
    version: int


class ReviewNote(TypedDict):
    file: str
    severity: Literal["blocker", "suggestion", "nit"]
    comment: str


class DevTeamState(TypedDict, total=False):
    # --- input ---
    session_id: str
    user_request: str
    edit_suggestion: dict | None     # set when the user edits a file in the artifact panel

    # --- working memory ---
    plan: str                                    # architect's plan / design doc
    files: dict[str, FileDict]                   # path -> current file
    review_notes: list[ReviewNote]                # replaced (not accumulated) each cycle
    qa_report: str
    qa_passed: bool

    # --- control flow ---
    cycle: int                                    # revision cycle counter
    max_cycles: int
    status: Literal[
        "planning", "coding", "reviewing", "testing",
        "revising", "approved", "failed"
    ]

    # --- transcript, used to stream progress back to the frontend ---
    log: Annotated[list[dict], operator.add]
