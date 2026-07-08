# Smart Meeting Follow-Up Agent

An agent that reads raw meeting notes, extracts action items, and creates a
task per item — but refuses to guess who owns something. If a note doesn't
name an owner, the item is flagged for a human instead of being silently
assigned.

Comes in two forms:

| File | What it is |
|---|---|
| `Smart_Meeting_FollowUp_Agent.ipynb` | Dev/exploration notebook — run the pipeline step by step |
| `app.py` + `index.html` + `requirements.txt` | A small Flask web app with a browser UI over the same pipeline |

Both use the same logic and the same provider switch (Boeing or OpenAI).

---

## How it works

```
Meeting notes (raw text)
        |
        v
  Extraction Agent (LLM)
        |
        v
  ["task", "owner", "deadline"] x N
        |
        v
  Owner Guardrail  ----(no owner)---->  flagged for a human
        |
     (has owner)
        v
  Default Deadline  (fills in "1 week out" if none was mentioned)
        |
        v
  create_task() per item
        |
        v
  send_notification() with a summary
```

The owner check is **plain Python, not an LLM judgment call** — a
convincingly worded note can't talk the agent into guessing a name.

---

## Project structure

```
.
├── Smart_Meeting_FollowUp_Agent.ipynb   # dev/exploration notebook
├── app.py                                # Flask backend + pipeline logic
├── index.html                            # browser UI (glassmorphism)
├── requirements.txt                      # Python dependencies
└── .env                                  # you create this — see below
```

---

## Setup

### 1. Create a virtual environment

**Windows (PowerShell / cmd):**
```bash
python -m venv venv
venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

If you're using VS Code, select this `venv` as both the Python interpreter
and the Jupyter kernel (Command Palette → "Python: Select Interpreter").

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` installs Flask, `python-dotenv`, `langchain-core`, and
`langchain-openai`. The Boeing packages (`boeing-chat-model`,
`boeing-embeddings`) are internal — install them separately from your
organization's package index if you're using the Boeing path:

```bash
pip install boeing-chat-model boeing-embeddings
```

### 3. Create a `.env` file

Create a file named `.env` in the same folder as `app.py`:

```ini
# Choose exactly one provider
LLM_PROVIDER=boeing          # or "openai"

# Required if LLM_PROVIDER=boeing
UDAL_PAT=your_udal_personal_access_token_here

# Required if LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-openai-key-here
```

Only the credentials for your chosen provider are required.

---

## Running it

### The web app

```bash
python app.py
```

You should see:
```
[SETUP] LLM_PROVIDER=boeing -> using BoeingChatModel (gpt-4o-mini).
 * Running on http://127.0.0.1:5000
```

Open **http://127.0.0.1:5000** in a browser. Paste meeting notes (or click
one of the two sample buttons) and click **Process notes**.

Sanity check the backend directly:
```bash
curl http://127.0.0.1:5000/api/health
# {"status": "ok", "provider": "boeing"}
```

### The notebook

Open `Smart_Meeting_FollowUp_Agent.ipynb` in VS Code or Jupyter, select the
`venv` kernel, and run the cells top to bottom. Section 0 handles the same
`.env` / provider setup as the app.

---

## Switching between Boeing and OpenAI

Change one line in `.env`:

```ini
LLM_PROVIDER=openai
```

That's the only change needed — both `app.py` and the notebook read
`LLM_PROVIDER` once at startup and build a single `chat_client` object.
Everything downstream (extraction, guardrails, task creation) calls
`chat_client` and has no idea which provider is behind it.

---

## API reference (web app)

### `POST /api/process`

**Request:**
```json
{ "notes": "Priya will update the API rate-limit docs by Friday..." }
```

**Response:**
```json
{
  "created_tasks": [
    {
      "task": "Update the API rate-limit docs",
      "owner": "Priya",
      "deadline": "Friday",
      "deadline_defaulted": false,
      "task_id": "TASK-0001"
    }
  ],
  "unassigned_items": [
    {
      "task": "Add a retry policy to the payment webhook handler",
      "owner": null,
      "deadline": "next Friday"
    }
  ],
  "notification_summary": "TASK-0001: ...\n1 action item(s) need a human to assign an owner.",
  "provider": "boeing"
}
```

### `GET /api/health`

Returns `{"status": "ok", "provider": "<boeing|openai>"}` — used by the UI
to show the connection status pill.

---

## Guardrail: no guessed owners

`apply_owner_guardrail()` in `app.py` splits extracted items into
`assigned` and `unassigned` based on whether the model returned a real name
for `"owner"`. Values like `null`, `"tbd"`, `"unknown"`, or an empty string
all route to `unassigned` — none of them ever reach `create_task()`.

This is deliberate: the extraction prompt already asks the model not to
guess, but prompts are not guarantees. The guardrail is the actual
enforcement, in plain Python, downstream of the LLM call.

---

## Swapping in real integrations

Everything under **Tools** in `app.py` is mocked so the project runs with
no external accounts:

```python
def create_task(title, owner, due_date) -> str: ...
def send_notification(summary) -> bool: ...
```

To go live, replace the bodies of these two functions with real calls —
for example, `create_task` → Jira/Asana/Linear API, `send_notification` →
Slack webhook or email. Nothing else in the pipeline needs to change; both
functions are called with the same arguments regardless of what's behind
them.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `UDAL_PAT not found in .env file` | `.env` missing, misnamed, or `LLM_PROVIDER=boeing` without `UDAL_PAT` set |
| `OPENAI_API_KEY not found in .env file` | Same as above, but for `LLM_PROVIDER=openai` |
| `ModuleNotFoundError: boeing_chat_model` | Boeing packages not installed — see Setup step 2 |
| Notes process but every item ends up "unassigned" | The model's raw output didn't parse as JSON — check the terminal for a `[EXTRACTION] Could not parse model output as JSON` log line |
| UI shows "Backend unreachable" | `app.py` isn't running, or is running on a different port than the browser is pointed at |

---

## Notes on scope

This project intentionally stays small: one extraction agent, two tools,
one hard guardrail. The README for the underlying problem statement lists
optional stretch goals (duplicate detection across meetings, priority
classification, per-owner workload warnings) if you want to extend it —
none of those are implemented here.