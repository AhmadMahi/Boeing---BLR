"""
Smart Meeting Follow-Up Agent - Flask backend.

Serves the UI (index.html) and one API endpoint that:
  1. Extracts action items from raw meeting notes using an LLM.
  2. Applies a guardrail that never guesses a missing owner - items with no
     named owner are flagged for a human instead of being assigned.
  3. Fills in a default deadline (one week out) for owned items with none.
  4. Creates a task per owned action item and sends a summary notification
     (both mocked - swap in real integrations, e.g. Jira/Asana + Slack).

Run with:
    python app.py
Then open http://127.0.0.1:5000 in a browser.
"""

import os
import re
import json
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage

load_dotenv()

# ============================================================
# LLM PROVIDER SWITCH
# Set LLM_PROVIDER to "boeing" or "openai" below, or via a
# LLM_PROVIDER value in your .env file. Only ONE provider needs
# working credentials at a time - everything below this block
# uses `chat_client` regardless of which one is active.
# ============================================================
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "boeing").lower()   # "boeing" or "openai"

if LLM_PROVIDER == "boeing":
    # -------------------- BOEING PATH --------------------
    from boeing_chat_model import BoeingChatModel

    UDAL_PAT = os.getenv("UDAL_PAT")
    if not UDAL_PAT:
        raise ValueError(
            "UDAL_PAT not found in .env file (required when LLM_PROVIDER=boeing)."
        )

    chat_client = BoeingChatModel(
        udal_pat=UDAL_PAT,
        model="gpt-4o-mini",
        max_tokens=500,
        temperature=0,
    )
    print("[SETUP] LLM_PROVIDER=boeing -> using BoeingChatModel (gpt-4o-mini).")

elif LLM_PROVIDER == "openai":
    # -------------------- OPENAI PATH --------------------
    from langchain_openai import ChatOpenAI

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    if not OPENAI_API_KEY:
        raise ValueError(
            "OPENAI_API_KEY not found in .env file (required when LLM_PROVIDER=openai)."
        )

    chat_client = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=OPENAI_API_KEY,
    )
    print("[SETUP] LLM_PROVIDER=openai -> using ChatOpenAI (gpt-4o-mini).")

else:
    raise ValueError(f"Unknown LLM_PROVIDER '{LLM_PROVIDER}'. Use 'boeing' or 'openai'.")


# ============================================================
# CONFIG
# ============================================================
DEFAULT_DEADLINE_DAYS = 7
NO_OWNER_VALUES = {"", "none", "null", "tbd", "unknown", "unassigned"}
NO_DEADLINE_VALUES = {"", "none", "null", "tbd"}

EXTRACTION_SYSTEM_PROMPT = """You are an assistant that extracts action items from raw meeting notes.

Return ONLY a JSON array, with no explanation and no markdown code fences.
Each element must be an object with exactly these keys:
  "task"     - a short description of the action item
  "owner"    - the person's name responsible, or null if no owner is named in the notes
  "deadline" - the deadline mentioned in the notes in any format, or null if none is mentioned

Do not guess an owner or a deadline that is not stated or clearly implied in the notes.
If the notes contain no action items, return an empty array: []
"""


# ============================================================
# TOOLS (mocked - replace with real integrations)
# ============================================================
_TASKS_DB = []


def create_task(title: str, owner: str, due_date: str) -> str:
    """Create a task for an action item and return its id."""
    task_id = f"TASK-{len(_TASKS_DB) + 1:04d}"
    _TASKS_DB.append({"id": task_id, "title": title, "owner": owner, "due_date": due_date})
    print(f"[TOOL: create_task] {task_id} | '{title}' -> {owner} (due {due_date})")
    return task_id


def send_notification(summary: str) -> bool:
    """Send a summary notification (mocked)."""
    print(f"[TOOL: send_notification]\n{summary}")
    return True


# ============================================================
# GUARDRAIL - never guess a missing owner
# ============================================================
def apply_owner_guardrail(items: list) -> tuple:
    """Split extracted items into (assigned, unassigned). Never assigns a guessed owner."""
    assigned, unassigned = [], []
    for item in items:
        owner = (item.get("owner") or "").strip()
        if owner.lower() in NO_OWNER_VALUES:
            unassigned.append(item)
        else:
            assigned.append(item)
    return assigned, unassigned


def apply_default_deadline(item: dict) -> dict:
    """Fill in a default deadline if none was mentioned in the notes."""
    deadline = (item.get("deadline") or "").strip()
    if deadline.lower() in NO_DEADLINE_VALUES:
        item["deadline"] = (datetime.now() + timedelta(days=DEFAULT_DEADLINE_DAYS)).strftime("%Y-%m-%d")
        item["deadline_defaulted"] = True
    else:
        item["deadline_defaulted"] = False
    return item


# ============================================================
# EXTRACTION AGENT
# ============================================================
def _strip_code_fences(text: str) -> str:
    return re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


def extract_action_items(notes: str) -> list:
    """Call the LLM and parse its response into a list of action item dicts."""
    response = chat_client.invoke([
        SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(content=notes),
    ])
    raw = _strip_code_fences(response.content)
    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            items = []
    except json.JSONDecodeError:
        print(f"[EXTRACTION] Could not parse model output as JSON:\n{raw}")
        items = []
    return items


# ============================================================
# PIPELINE
# ============================================================
def process_meeting_notes(notes: str) -> dict:
    items = extract_action_items(notes)
    assigned, unassigned = apply_owner_guardrail(items)

    created_tasks = []
    for item in assigned:
        item = apply_default_deadline(item)
        task_id = create_task(item["task"], item["owner"], item["deadline"])
        created_tasks.append({**item, "task_id": task_id})

    summary_lines = [
        f"{t['task_id']}: {t['task']} -> {t['owner']} (due {t['deadline']})"
        for t in created_tasks
    ]
    if unassigned:
        summary_lines.append(
            f"{len(unassigned)} action item(s) need a human to assign an owner."
        )
    summary = "\n".join(summary_lines) if summary_lines else "No action items found in these notes."

    send_notification(summary)

    return {
        "created_tasks": created_tasks,
        "unassigned_items": unassigned,
        "notification_summary": summary,
        "provider": LLM_PROVIDER,
    }


# ============================================================
# FLASK APP
# ============================================================
app = Flask(__name__, static_folder=".", static_url_path="")


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/api/process", methods=["POST"])
def api_process():
    data = request.get_json(force=True, silent=True) or {}
    notes = (data.get("notes") or "").strip()
    if not notes:
        return jsonify({"error": "The 'notes' field is required."}), 400
    try:
        result = process_meeting_notes(notes)
        return jsonify(result)
    except Exception as exc:  # surfaced to the UI rather than a bare 500 page
        return jsonify({"error": str(exc)}), 500


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "provider": LLM_PROVIDER})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
