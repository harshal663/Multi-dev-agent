from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app.redis_client import load_session

router = APIRouter(prefix="/api/sessions", tags=["files"])


@router.get("/{session_id}/files/{path:path}")
async def get_file(session_id: str, path: str):
    session = await load_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    f = session.files.get(path)
    if not f:
        raise HTTPException(404, f"Unknown file: {path}")
    return f
