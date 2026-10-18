from __future__ import annotations
import json
import re
from langchain_core.messages import SystemMessage, HumanMessage
from app.agents.llm import architect_llm, coder_llm, reviewer_llm, qa_llm
from app.agents.state import DevTeamState


def _extract_json(text: str) -> dict:
    """Best-effort JSON extraction from an LLM response."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"(\{.*\})", text, re.DOTALL)
        if brace:
            text = brace.group(1)
    return json.loads(text)


def _log(state: DevTeamState, node: str, message: str, **extra) -> dict:
    return {"log": [{"node": node, "message": message, **extra}]}


# --------------------------------------------------------------------------
# 1. ARCHITECT — turns a user request into a short design plan + file list
# --------------------------------------------------------------------------
ARCHITECT_SYSTEM = """You are the Architect on an autonomous software dev team.
Given a user's feature/bug request, produce a compact implementation plan.
Respond ONLY with JSON of this shape, no prose outside the JSON:
{
  "plan": "<3-8 sentence design summary: approach, key modules, edge cases>",
  "files": [{"path": "src/example.py", "language": "python", "purpose": "one line"}]
}
Keep the file list minimal (usually 1-4 files) and appropriate to the request's scope."""


def architect_node(state: DevTeamState) -> dict:
    llm = architect_llm()
    resp = llm.invoke([
        SystemMessage(content=ARCHITECT_SYSTEM),
        HumanMessage(content=f"User request:\n{state['user_request']}"),
    ])
    parsed = _extract_json(resp.content)
    return {
        "plan": parsed["plan"],
        "status": "coding",
        "cycle": 0,
        **_log(state, "architect", f"Plan ready: {len(parsed.get('files', []))} file(s) scoped."),
        # stash the proposed file skeleton in the plan text; coder fleshes it out
        "files": {
            f["path"]: {"path": f["path"], "language": f.get("language", "text"), "content": "", "version": 0}
            for f in parsed.get("files", [])
        },
    }


# --------------------------------------------------------------------------
# 2. CODER — writes/updates file contents, and incorporates review feedback
#    and/or a user's manual edit suggestion on subsequent cycles.
# --------------------------------------------------------------------------
CODER_SYSTEM = """You are the Coder on an autonomous software dev team.
Write complete, runnable file contents that satisfy the architect's plan.
If review notes or a user edit suggestion are provided, address every one of them.
Respond ONLY with JSON of this shape:
{ "files": [{"path": "...", "language": "...", "content": "<full file content>"}] }
Always return FULL file contents, never a diff or partial snippet."""


def coder_node(state: DevTeamState) -> dict:
    llm = coder_llm()
    context = [f"Plan:\n{state.get('plan', '')}"]
    context.append("Files to produce/update:\n" + "\n".join(state.get("files", {}).keys()))

    if state.get("review_notes"):
        notes = "\n".join(f"- [{n['severity']}] {n['file']}: {n['comment']}" for n in state["review_notes"])
        context.append(f"Reviewer/QA feedback to address:\n{notes}")

    if state.get("edit_suggestion"):
        es = state["edit_suggestion"]
        context.append(
            f"The user manually edited `{es['path']}` and left this note: "
            f"\"{es.get('note', '(no note)')}\"\n"
            f"Their edited version:\n```\n{es['edited_content']}\n```\n"
            f"Reconcile this with the rest of the codebase, keep it working, "
            f"and preserve the user's intent."
        )

    existing = "\n\n".join(
        f"--- current {p} (v{f['version']}) ---\n{f['content']}" for p, f in state.get("files", {}).items() if f["content"]
    )
    if existing:
        context.append(f"Current file contents:\n{existing}")

    resp = llm.invoke([
        SystemMessage(content=CODER_SYSTEM),
        HumanMessage(content="\n\n".join(context)),
    ])
    parsed = _extract_json(resp.content)

    files = dict(state.get("files", {}))
    for f in parsed["files"]:
        prev_version = files.get(f["path"], {}).get("version", 0)
        files[f["path"]] = {
            "path": f["path"],
            "language": f.get("language", files.get(f["path"], {}).get("language", "text")),
            "content": f["content"],
            "version": prev_version + 1,
        }

    return {
        "files": files,
        "status": "reviewing",
        "edit_suggestion": None,          # consumed
        "review_notes": [],               # cleared; reviewer/qa will repopulate if needed — see note below
        **_log(state, "coder", f"Wrote {len(parsed['files'])} file(s), cycle {state.get('cycle', 0)}."),
    }


# --------------------------------------------------------------------------
# 3. CODE REVIEWER — static/quality review, produces structured notes
# --------------------------------------------------------------------------
REVIEWER_SYSTEM = """You are the Code Reviewer on an autonomous software dev team.
Review the given files for correctness, security, readability, and adherence to the plan.
Respond ONLY with JSON:
{
  "approved": true|false,
  "notes": [{"file": "path", "severity": "blocker|suggestion|nit", "comment": "..."}]
}
Only "blocker" severity should block approval. Be concrete and reference line-level issues where relevant."""


def reviewer_node(state: DevTeamState) -> dict:
    llm = reviewer_llm()
    files_blob = "\n\n".join(f"--- {p} ---\n{f['content']}" for p, f in state["files"].items())
    resp = llm.invoke([
        SystemMessage(content=REVIEWER_SYSTEM),
        HumanMessage(content=f"Plan:\n{state.get('plan','')}\n\nFiles:\n{files_blob}"),
    ])
    parsed = _extract_json(resp.content)
    notes = parsed.get("notes", [])
    return {
        "review_notes": notes,
        "status": "testing" if parsed.get("approved") else "revising",
        **_log(state, "reviewer", f"{'Approved' if parsed.get('approved') else 'Requested changes'} "
                                   f"({len(notes)} note(s))."),
    }


# --------------------------------------------------------------------------
# 4. QA TESTER — reasons about test coverage / runs sandboxed tests if any
# --------------------------------------------------------------------------
QA_SYSTEM = """You are the QA Tester on an autonomous software dev team.
Given the plan and final files, decide if this implementation is ready to ship.
Consider correctness against the plan, obvious runtime errors, and missing edge cases.
Respond ONLY with JSON:
{ "passed": true|false, "report": "<what you checked and the verdict>",
  "notes": [{"file": "path", "severity": "blocker|suggestion", "comment": "..."}] }"""


def qa_node(state: DevTeamState) -> dict:
    llm = qa_llm()
    files_blob = "\n\n".join(f"--- {p} ---\n{f['content']}" for p, f in state["files"].items())
    resp = llm.invoke([
        SystemMessage(content=QA_SYSTEM),
        HumanMessage(content=f"Plan:\n{state.get('plan','')}\n\nFiles:\n{files_blob}"),
    ])
    parsed = _extract_json(resp.content)
    passed = bool(parsed.get("passed"))
    next_cycle = state.get("cycle", 0) + 1
    return {
        "qa_report": parsed.get("report", ""),
        "qa_passed": passed,
        "review_notes": parsed.get("notes", []) if not passed else [],
        "cycle": next_cycle,
        "status": "approved" if passed else "revising",
        **_log(state, "qa", f"QA {'passed' if passed else 'failed'} on cycle {next_cycle}."),
    }
