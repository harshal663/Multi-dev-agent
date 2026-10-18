from __future__ import annotations
import uuid
import time
from typing import Literal, Optional
from pydantic import BaseModel, Field

Role = Literal["user", "architect", "coder", "reviewer", "qa", "system"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class CodeFile(BaseModel):
    path: str                      # e.g. "src/app.py"
    language: str = "python"
    content: str
    version: int = 1
    last_edited_by: Literal["agent", "user"] = "agent"


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    role: Role
    content: str
    created_at: float = Field(default_factory=time.time)
    files: list[CodeFile] = Field(default_factory=list)   
    meta: dict = Field(default_factory=dict)              


class ChatSession(BaseModel):
    id: str = Field(default_factory=lambda: new_id("chat"))
    title: str = "New chat"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    messages: list[ChatMessage] = Field(default_factory=list)
    files: dict[str, CodeFile] = Field(default_factory=dict)   


class NewMessageRequest(BaseModel):
    content: str


class EditSuggestionRequest(BaseModel):
    """User-suggested edit to a specific artifact file, sent back into the agent loop."""
    path: str
    edited_content: str
    note: Optional[str] = None   # user's explanation of the requested change


class AgentEvent(BaseModel):
    """Streamed over SSE/WebSocket while the graph runs."""
    type: Literal["node_start", "node_end", "message", "file_update", "done", "error"]
    node: Optional[str] = None
    data: dict = Field(default_factory=dict)
