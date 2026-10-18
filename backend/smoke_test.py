"""
End-to-end smoke test: mocks the Claude calls (so it runs with no API key/network)
but exercises the *real* FastAPI app, the *real* LangGraph graph, and the *real*
Redis server, across two full turns (initial request + a user edit suggestion).
"""
import os
os.environ["MIO_GROQ_API_KEY"] = "test-key"
os.environ["MIO_REDIS_URL"] = "redis://localhost:6379/0"

import json
import asyncio
from types import SimpleNamespace
from fastapi.testclient import TestClient

import app.agents.nodes as nodes_mod

# ---- mock Claude responses per role -------------------------------------------------
class FakeLLM:
    def __init__(self, payload_fn):
        self.payload_fn = payload_fn

    def invoke(self, messages):
        content = self.payload_fn(messages)
        return SimpleNamespace(content=json.dumps(content))


def architect_payload(messages):
    return {
        "plan": "Build a single-file FastAPI health-check endpoint returning {status: ok}.",
        "files": [{"path": "main.py", "language": "python", "purpose": "health endpoint"}],
    }


CALL_COUNT = {"coder": 0}


def coder_payload(messages):
    CALL_COUNT["coder"] += 1
    joined = "\n".join(getattr(m, "content", "") for m in messages)
    if "edited version" in joined:
        content = "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health():\n    return {'status': 'ok', 'edited': True}\n"
    else:
        content = "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health():\n    return {'status': 'ok'}\n"
    return {"files": [{"path": "main.py", "language": "python", "content": content}]}


def reviewer_payload(messages):
    return {"approved": True, "notes": []}


def qa_payload(messages):
    return {"passed": True, "report": "Health endpoint returns 200 with expected payload.", "notes": []}


nodes_mod.architect_llm = lambda: FakeLLM(architect_payload)
nodes_mod.coder_llm = lambda: FakeLLM(coder_payload)
nodes_mod.reviewer_llm = lambda: FakeLLM(reviewer_payload)
nodes_mod.qa_llm = lambda: FakeLLM(qa_payload)

# rebuild the graph so it picks up nodes bound to the (now-patched) module functions
from app.agents import graph as graph_mod
import importlib
importlib.reload(graph_mod)

import app.routes.chat as chat_mod
chat_mod.dev_team_graph = graph_mod.dev_team_graph

from app.main import app as fastapi_app

client = TestClient(fastapi_app)


def parse_sse(resp_text: str):
    events = []
    cur_event, cur_data = None, None
    for line in resp_text.splitlines():
        if line.startswith("event:"):
            cur_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            cur_data = line.split(":", 1)[1].strip()
        elif line == "" and cur_event:
            events.append((cur_event, cur_data))
            cur_event, cur_data = None, None
    if cur_event:
        events.append((cur_event, cur_data))
    return events


def main():
    with client:
        print("1. Health check...")
        r = client.get("/api/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"
        print("   OK")

        print("2. Create session...")
        r = client.post("/api/sessions")
        assert r.status_code == 200, r.text
        session = r.json()
        sid = session["id"]
        print(f"   OK session_id={sid}")

        print("3. List sessions (sidebar)...")
        r = client.get("/api/sessions")
        assert r.status_code == 200
        assert any(s["id"] == sid for s in r.json())
        print("   OK")

        print("4. Send message -> run graph (architect->coder->reviewer->qa)...")
        with client.stream("POST", f"/api/sessions/{sid}/messages", json={"content": "Add a /health endpoint"}) as r:
            assert r.status_code == 200, r.text
            text = "".join(r.iter_text())
        events = parse_sse(text)
        kinds = [e for e, _ in events]
        print("   events:", kinds)
        assert "done" in kinds, f"no 'done' event, got {kinds}"
        done_data = json.loads([d for e, d in events if e == "done"][0])
        assert "main.py" in [f["path"] for f in done_data["message"]["files"]]
        print("   OK — file produced:", done_data["message"]["files"][0]["path"],
              "status:", done_data["message"]["meta"]["status"])

        print("5. Fetch full session, confirm persistence...")
        r = client.get(f"/api/sessions/{sid}")
        assert r.status_code == 200
        full = r.json()
        assert "main.py" in full["files"]
        assert full["files"]["main.py"]["version"] == 1
        print("   OK — persisted file version:", full["files"]["main.py"]["version"])

        print("6. Submit a user edit suggestion on main.py (artifact panel flow)...")
        edited = full["files"]["main.py"]["content"].replace("'ok'", "'ok-manually-edited'")
        with client.stream("POST", f"/api/sessions/{sid}/files/edit",
                            json={"path": "main.py", "edited_content": edited, "note": "tweak status string"}) as r:
            assert r.status_code == 200, r.text
            text2 = "".join(r.iter_text())
        events2 = parse_sse(text2)
        kinds2 = [e for e, _ in events2]
        print("   events:", kinds2)
        assert "done" in kinds2
        done_data2 = json.loads([d for e, d in events2 if e == "done"][0])
        print("   OK — status:", done_data2["message"]["meta"]["status"])

        print("7. Confirm version bumped after edit-suggestion cycle...")
        r = client.get(f"/api/sessions/{sid}")
        full2 = r.json()
        assert full2["files"]["main.py"]["version"] == 2, full2["files"]["main.py"]["version"]
        print("   OK — version now:", full2["files"]["main.py"]["version"])

        print("8. Delete session...")
        r = client.delete(f"/api/sessions/{sid}")
        assert r.status_code == 200
        r = client.get(f"/api/sessions/{sid}")
        assert r.status_code == 404
        print("   OK")

        print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
