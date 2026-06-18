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
    }


class _Session:
    """One websocket connection: a persistent agent conversation."""

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

    def emit(self, event: dict):
        """Thread-safe push from the agent's (sync) on_event into the queue."""
        self.loop.call_soon_threadsafe(self.out.put_nowait, event)

    async def _drain(self):
        """Forward queued agent events to the websocket until told to stop."""
        while True:
            event = await self.out.get()
            if event is None:        # sentinel = turn finished
                return
            await self.ws.send_text(json.dumps(event))

    async def run_turn(self, text: str, model_id: str | None):
        if model_id:
            self.model_id = model_id
        # Phase 0: auto-allow every tool call. Interactive dialogs come later.
        permissions.set_mode(permissions.MODE_AUTO)

        drainer = asyncio.ensure_future(self._drain())
        try:
            visible, self.conversation = await agent.run_agent(
                text,
                self.conversation,
                self.memory_md,
                self.user_md,
                self.model_id,
                self.deepcode_md,
                self.project_md,
                swarm_mode=True,         # silence console; emit events only
                on_event=self.emit,
            )
            await self.ws.send_text(json.dumps(
                {"type": "turn_done", "text": visible}))
        except Exception as e:
            await self.ws.send_text(json.dumps(
                {"type": "error", "text": str(e)}))
        finally:
            self.out.put_nowait(None)    # stop the drainer
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
            if msg.get("type") == "chat":
                await session.run_turn(msg.get("text", ""), msg.get("model"))
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
