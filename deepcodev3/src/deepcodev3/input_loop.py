"""Persistent bottom-anchored input for the DeepCode REPL.

Design (chosen to reuse the existing rich renderer and avoid a full-screen
prompt_toolkit Application, which is fragile on Win32 — see
ultracode-workflows-lessons):

- `patch_stdout(raw=True)` is held for the WHOLE session. Every rich write to
  stdout is inserted ABOVE the live input line, so the prompt stays pinned at
  the bottom while the agent streams output above it.
- A dedicated daemon thread runs `session.prompt()` in a loop and pushes each
  submitted line onto a thread-safe queue. The async main loop pulls from that
  queue. Because input runs in its own thread, you can type (and queue) the
  next message while the agent is still streaming.
- Multiple submissions stack: each Enter enqueues another message; they are
  processed in order once the current turn finishes.
- Esc while a turn is running fires an interrupt callback (cancels the in-flight
  asyncio task). Esc when idle does nothing.

The InputController owns the PromptSession and the queue. main_loop() asks it
for the next line via `await get_line()`.
"""
from __future__ import annotations

import asyncio
import queue as _queue
import threading
from typing import Callable, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from . import renderer


class InputController:
    def __init__(self, session: PromptSession, prompt_fn: Callable[[], str]):
        """
        session   : the configured PromptSession (history, completer, kb, toolbar).
        prompt_fn : returns the prompt string to display (recomputed each line so
                    mode/agent indicators stay current).
        """
        self.session = session
        self._prompt_fn = prompt_fn
        self._q: "_queue.Queue[Optional[str]]" = _queue.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._patch_cm = None
        # queued messages waiting to be processed (for toolbar display)
        self.pending: list[str] = []
        # interrupt callback: called (in the asyncio loop) when Esc is pressed
        # while a turn is running. Set by main_loop around each agent turn.
        self._interrupt_cb: Optional[Callable[[], None]] = None
        self._busy = False
        # When paused, the input thread stops calling session.prompt() so a
        # blocking msvcrt picker (quiz/permission/session browser) can own the
        # keyboard without contention.
        self._paused = threading.Event()
        self._resumed = threading.Event()
        self._resumed.set()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        # Hold patch_stdout for the entire session so background prints insert
        # above the input line. renderer.console must point at the patched
        # stdout for the whole duration.
        self._patch_cm = patch_stdout(raw=True)
        self._patch_cm.__enter__()
        import sys
        renderer.console.file = sys.stdout
        self._thread = threading.Thread(target=self._run, name="deepcode-input", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        # unblock get_line waiters
        self._q.put(None)
        if self._patch_cm is not None:
            try:
                self._patch_cm.__exit__(None, None, None)
            except Exception:
                pass
            self._patch_cm = None
            renderer.console.file = renderer._real_console_file

    # ── interrupt wiring ──────────────────────────────────────────────────────

    def set_interrupt(self, cb: Optional[Callable[[], None]]):
        """Register the callback Esc should fire while a turn runs (or None)."""
        self._interrupt_cb = cb
        self._busy = cb is not None

    def fire_interrupt(self):
        """Called from the key binding (input thread). Schedules the interrupt
        callback onto the asyncio loop. No-op if idle."""
        cb = self._interrupt_cb
        if cb is None or self._loop is None:
            return
        self._loop.call_soon_threadsafe(cb)

    # ── input thread ──────────────────────────────────────────────────────────

    def _run(self):
        while not self._stop.is_set():
            # honor pause: don't touch the keyboard while a picker owns it
            if self._paused.is_set():
                self._resumed.set()
                while self._paused.is_set() and not self._stop.is_set():
                    threading.Event().wait(0.03)
                self._resumed.clear()
                continue
            try:
                line = self.session.prompt(self._prompt_fn())
            except (EOFError, KeyboardInterrupt):
                # Ctrl-D / Ctrl-C in the input thread → signal exit
                self._q.put(None)
                return
            except Exception:
                # never let the input thread die silently on a transient error
                continue
            if self._stop.is_set():
                return
            # if we were paused mid-prompt (app.exit forced a return), discard
            if self._paused.is_set():
                continue
            self._q.put(line)

    # ── pause/resume for blocking pickers ─────────────────────────────────────

    def pause(self):
        """Stop the input thread from reading the keyboard so a blocking msvcrt
        picker can take over. Exits any in-progress prompt app, then waits until
        the thread has actually parked."""
        self._paused.set()
        # break the thread out of a blocking session.prompt()
        try:
            app = self.session.app
            if app is not None and app.is_running:
                app.exit(result="")
        except Exception:
            pass
        # wait (briefly) for the thread to acknowledge the pause
        for _ in range(50):
            if self._resumed.is_set() and not self._reading():
                break
            threading.Event().wait(0.01)

    def resume(self):
        self._paused.clear()

    def _reading(self) -> bool:
        try:
            app = self.session.app
            return bool(app is not None and app.is_running)
        except Exception:
            return False

    # ── async consumption ─────────────────────────────────────────────────────

    async def get_line(self) -> Optional[str]:
        """Await the next submitted line. Returns None on shutdown/EOF."""
        assert self._loop is not None
        # poll the thread-safe queue without blocking the event loop
        while True:
            try:
                return self._q.get_nowait()
            except _queue.Empty:
                await asyncio.sleep(0.02)

    async def read_one(self) -> Optional[str]:
        """Read a single line using the SAME persistent input bar — used for
        one-off prompts (quiz free-text, confirmations). The caller should print
        the question above the bar first. Returns the next submitted line, or
        None on shutdown. (There is only ever one input bar; we never open a
        second concurrent session.prompt.)"""
        return await self.get_line()
