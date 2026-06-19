"""DeepCode v3 — GUI backend bridge.

A thin FastAPI + websocket server that wraps the existing terminal-agent core
(the `deepcodev3` package) so an Electron/React frontend can drive it.

Design:
- The core's `agent.run_agent` already takes an `on_event(dict)` callback and a
  `swarm_mode` flag that silences all console output. We run with
  `swarm_mode=True` and pipe every event over the websocket as JSON. That gives
  the frontend structured progress (thinking / delta / tool_call / tool_result /
  tokens / response) with almost no change to the agent.
- Phase 0 auto-allows tool calls (permissions.MODE_AUTO). Interactive permission
  dialogs over the socket come in a later phase.

Run:  python -m server   (or: uvicorn server.server:app)
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path

# Make the core importable without installing it.
_CORE_SRC = Path(__file__).resolve().parent.parent / "deepcodev3" / "src"
if str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from deepcodev3 import agent, models, storage, permissions  # noqa: E402

app = FastAPI(title="DeepCode v3 bridge")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # local desktop app; tighten if ever hosted
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/models")
async def list_models():
    """Model registry for the frontend's model picker."""
    return {
        "default": models.DEFAULT_MODEL,
        "models": [
            {
                "id": m["id"],
                "name": m["name"],
                "provider": m.get("provider", ""),
                "tier": m.get("tier", ""),
            }
            for m in models.MODELS
        ],
    }


@app.get("/api/config")
async def get_config():
    cfg = storage.load_config()
    return {
        "model": cfg.get("model", models.DEFAULT_MODEL),
        "accent": cfg.get("accent", "orange"),
        "agent": cfg.get("agent", True),
        "reasoning": cfg.get("reasoning", None),
    }


@app.get("/api/sessions")
async def list_sessions():
    """Conversation history for the sidebar (shared with the terminal). Newest
    first; messages omitted here — fetched per-session via /api/sessions/{id}."""
    sessions = storage.load_history()
    out = []
    for s in reversed(sessions[-100:]):
        out.append({
            "id": s.get("id"),
            "title": s.get("title") or "Untitled",
            "model": s.get("model", ""),
            "created": s.get("created", ""),
            "count": len(s.get("messages", [])),
        })
    return {"sessions": out}


@app.get("/api/sessions/{sid}")
async def get_session(sid: str):
    sessions = storage.load_history()
    s = next((x for x in sessions if x.get("id") == sid), None)
    if s is None:
        return {"error": "not found"}
    return s


def _persist(session_dict: dict):
    """Merge a session into history by id (no dup), keep last 100."""
    sessions = storage.load_history()
    merged = [s for s in sessions if s.get("id") != session_dict.get("id")]
    merged.append(session_dict)
    storage.save_history(merged[-100:])


class _Session:
    """One websocket connection. Owns a persistent agent conversation backed by
    a stored session dict (shared history with the terminal)."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.conversation: list[dict] = []
        self.model_id = models.DEFAULT_MODEL
        self.loop = asyncio.get_event_loop()
        self.out: asyncio.Queue = asyncio.Queue()
        self.memory_md = storage.load_memory_md()
        self.user_md = storage.load_user_md()
        self.deepcode_md = storage.load_deepcode_md()
        self.project_md = storage.load_project_memory_md()
        self.store = storage.new_session(self.model_id)  # persisted session dict

    def load(self, sid: str):
        """Resume an existing stored session into this connection."""
        sessions = storage.load_history()
        s = next((x for x in sessions if x.get("id") == sid), None)
        if not s:
            return
        self.store = s
        self.model_id = s.get("model", self.model_id)
        self.conversation = [
            {"role": m["role"], "content": m["content"]}
            for m in s.get("messages", [])
            if m["role"] in ("user", "assistant")
        ]

    def reset(self):
        """Start a fresh conversation."""
        self.store = storage.new_session(self.model_id)
        self.conversation = []

    def emit(self, event: dict):
        """Thread-safe push from the agent's (sync) on_event into the queue."""
        self.loop.call_soon_threadsafe(self.out.put_nowait, event)

    async def _drain(self):
        while True:
            event = await self.out.get()
            if event is None:
                return
            await self.ws.send_text(json.dumps(event))

    async def run_turn(self, text: str, opts: dict):
        model_id = opts.get("model")
        if model_id:
            self.model_id = model_id
        reasoning_level = opts.get("reasoning")  # None/"off"/low/middle/high/ultra
        # auto-allow tool calls in the GUI for now (interactive dialogs later)
        permissions.set_mode(permissions.MODE_AUTO)

        self.store.setdefault("messages", []).append(
            {"role": "user", "content": text, "model": self.model_id})

        drainer = asyncio.ensure_future(self._drain())
        try:
            effective = text
            # optional reasoning pre-pass (mirrors the terminal's reasoning mode)
            if reasoning_level and reasoning_level != "off":
                self.emit({"type": "reasoning", "level": reasoning_level})
                from deepcodev3 import reasoning, system_prompt
                plan = await reasoning.run_reasoning(
                    text, self.model_id, reasoning_level,
                    system_prompt.SYSTEM_PROMPT, memory_block="", quiet=True)
                if plan:
                    effective = f"{text}\n\n[Your private reasoning/plan:\n{plan}\n]"

            visible, self.conversation = await agent.run_agent(
                effective,
                self.conversation,
                self.memory_md,
                self.user_md,
                self.model_id,
                self.deepcode_md,
                self.project_md,
                swarm_mode=True,
                on_event=self.emit,
            )
            self.store["messages"].append(
                {"role": "assistant", "content": visible, "model": self.model_id})
            if not self.store.get("title") and visible:
                self.store["title"] = text[:40]
            _persist(self.store)
            await self.ws.send_text(json.dumps(
                {"type": "turn_done", "text": visible, "sessionId": self.store.get("id")}))
        except Exception as e:
            await self.ws.send_text(json.dumps(
                {"type": "error", "text": str(e)}))
        finally:
            self.out.put_nowait(None)
            await drainer


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session = _Session(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps(
                    {"type": "error", "text": "bad json"}))
                continue
            mtype = msg.get("type")
            if mtype == "chat":
                await session.run_turn(msg.get("text", ""), msg.get("opts", {}))
            elif mtype == "load":
                session.load(msg.get("id", ""))
                await ws.send_text(json.dumps(
                    {"type": "loaded", "session": session.store}))
            elif mtype == "new":
                session.reset()
                await ws.send_text(json.dumps(
                    {"type": "loaded", "session": session.store}))
            else:
                await ws.send_text(json.dumps(
                    {"type": "error", "text": f"unknown msg type {msg.get('type')!r}"}))
    except WebSocketDisconnect:
        return


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
