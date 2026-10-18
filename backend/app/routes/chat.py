"""
REST + SSE endpoints for chat sessions.

POST   /api/sessions                     create a new chat
GET    /api/sessions                     list chats for the sidebar
GET    /api/sessions/{id}                full chat (messages + files)
DELETE /api/sessions/{id}                delete a chat
POST   /api/sessions/{id}/messages       send a user message -> streams agent run (SSE)
POST   /api/sessions/{id}/files/edit     user-suggested edit on an artifact -> streams agent run (SSE)
"""
from __future__ import annotations
import json
import time
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.models import ChatSession, ChatMessage, CodeFile, NewMessageRequest, EditSuggestionRequest
from app.redis_client import save_session, load_session, list_sessions, delete_session
from app.agents.graph import dev_team_graph
from app.agents.state import DevTeamState

router = APIRouter(prefix="/api/sessions", tags=["chat"])


@router.post("")
async def create_session():
    session = ChatSession()
    await save_session(session)
    return session


@router.get("")
async def get_sessions():
    return await list_sessions()


@router.get("/{session_id}")
async def get_session(session_id: str):
    session = await load_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session


@router.delete("/{session_id}")
async def remove_session(session_id: str):
    await delete_session(session_id)
    return {"ok": True}


def _title_from_request(text: str) -> str:
    text = text.strip().replace("\n", " ")
    return (text[:48] + "…") if len(text) > 48 else (text or "New chat")


async def _run_graph_and_stream(session: ChatSession, initial_state: DevTeamState):
    async def event_gen():
        final_state: dict = dict(initial_state)
        try:
            async for step in dev_team_graph.astream(initial_state, stream_mode="updates"):
                for node_name, update in step.items():
                    final_state.update(update)
                    log_entries = update.get("log", [])
                    for entry in log_entries:
                        yield {
                            "event": "node_update",
                            "data": json.dumps({"node": node_name, **entry}),
                        }
                    if "files" in update:
                        yield {
                            "event": "files_update",
                            "data": json.dumps({
                                "node": node_name,
                                "files": list(update["files"].values()),
                            }),
                        }

            # --- persist results onto the chat session ---
            files = final_state.get("files", {})
            session.files = {
                p: CodeFile(path=f["path"], language=f["language"], content=f["content"], version=f["version"])
                for p, f in files.items()
            }
            summary = final_state.get("qa_report") or final_state.get("plan") or "Done."
            status = final_state.get("status", "approved")
            assistant_msg = ChatMessage(
                role="qa" if final_state.get("qa_report") else "architect",
                content=f"**Status:** {status}\n\n{summary}",
                files=list(session.files.values()),
                meta={"cycle": final_state.get("cycle", 0), "status": status},
            )
            session.messages.append(assistant_msg)
            session.updated_at = time.time()
            if session.title == "New chat" and session.messages:
                session.title = _title_from_request(session.messages[0].content)
            await save_session(session)

            yield {"event": "done", "data": json.dumps({
                "message": assistant_msg.model_dump(),
                "session_id": session.id,
            })}
        except Exception as e:  # surface agent/LLM errors to the frontend instead of hanging
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(event_gen())


@router.post("/{session_id}/messages")
async def send_message(session_id: str, body: NewMessageRequest):
    session = await load_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    session.messages.append(ChatMessage(role="user", content=body.content))
    await save_session(session)

    initial_state: DevTeamState = {
        "session_id": session_id,
        "user_request": body.content,
        "files": {p: f.model_dump() for p, f in session.files.items()},
        "cycle": 0,
        "status": "planning",
        "log": [],
        "review_notes": [],
    }
    return await _run_graph_and_stream(session, initial_state)


@router.post("/{session_id}/files/edit")
async def suggest_edit(session_id: str, body: EditSuggestionRequest):
    """Called when the user edits code in the artifact side panel and hits 'Suggest edit'."""
    session = await load_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if body.path not in session.files:
        raise HTTPException(404, f"Unknown file: {body.path}")

    note = body.note or "User manually edited this file in the artifact panel."
    session.messages.append(ChatMessage(
        role="user",
        content=f"Suggested an edit to `{body.path}`: {note}",
    ))
    await save_session(session)

    initial_state: DevTeamState = {
        "session_id": session_id,
        "user_request": f"Incorporate the user's manual edit to {body.path}.",
        "files": {p: f.model_dump() for p, f in session.files.items()},
        "edit_suggestion": {"path": body.path, "edited_content": body.edited_content, "note": note},
        "cycle": 0,
        "status": "coding",
        "log": [],
        "review_notes": [],
    }
    return await _run_graph_and_stream(session, initial_state)
