from __future__ import annotations
import asyncio
import redis.asyncio as aioredis
from app.config import settings
from app.models import ChatSession

_redis: aioredis.Redis | None = None
_redis_loop: asyncio.AbstractEventLoop | None = None

SESSIONS_INDEX = "mio:sessions:index"   # sorted set: session_id -> updated_at


def get_redis() -> aioredis.Redis:
    """
    Lazily creates (and re-creates, if the running event loop has changed —
    e.g. under test clients or certain worker setups) an async Redis client.
    A single long-lived event loop, as under normal uvicorn operation, keeps
    this to one client for the process lifetime.
    """
    global _redis, _redis_loop
    current_loop = asyncio.get_event_loop()
    if _redis is None or _redis_loop is not current_loop:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        _redis_loop = current_loop
    return _redis


async def save_session(session: ChatSession) -> None:
    r = get_redis()
    key = f"mio:session:{session.id}"
    await r.set(key, session.model_dump_json(), ex=settings.session_ttl_seconds)
    await r.zadd(SESSIONS_INDEX, {session.id: session.updated_at})


async def load_session(session_id: str) -> ChatSession | None:
    r = get_redis()
    raw = await r.get(f"mio:session:{session_id}")
    if not raw:
        return None
    return ChatSession.model_validate_json(raw)


async def list_sessions(limit: int = 100) -> list[dict]:
    """Return lightweight session summaries for the sidebar, newest first."""
    r = get_redis()
    ids = await r.zrevrange(SESSIONS_INDEX, 0, limit - 1)
    if not ids:
        return []
    pipe = r.pipeline()
    for sid in ids:
        pipe.get(f"mio:session:{sid}")
    raw_sessions = await pipe.execute()
    summaries = []
    for raw in raw_sessions:
        if not raw:
            continue
        s = ChatSession.model_validate_json(raw)
        summaries.append({
            "id": s.id,
            "title": s.title,
            "updated_at": s.updated_at,
            "message_count": len(s.messages),
        })
    return summaries


async def delete_session(session_id: str) -> None:
    r = get_redis()
    await r.delete(f"mio:session:{session_id}")
    await r.zrem(SESSIONS_INDEX, session_id)
