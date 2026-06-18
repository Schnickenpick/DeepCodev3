"""Persistent bottom-anchored input bar for the DeepCode REPL (Claude-Code style).

The input is a small prompt_toolkit Application — a bordered TextArea (the box)
with a mode line beneath it — rendered at the bottom of the terminal. It is NOT
full-screen: `patch_stdout(raw=True)` is held for the whole session, so every
rich write to stdout is inserted ABOVE the bar and the bar stays pinned at the
bottom while the agent streams.

The Application runs in a daemon thread; each submitted line is pushed onto a
thread-safe queue the async main loop pulls from. So you can type/queue the next
message while the agent is still streaming. Multiple submissions stack and are
processed in order. Esc on an empty box interrupts the running turn.

main_loop() drives it: `await get_line()` for the next message; set_interrupt()
to register what Esc cancels; pause()/resume() to hand the keyboard to a
blocking msvcrt picker.
"""
from __future__ import annotations

import asyncio
import queue as _queue
import threading
from typing import Callable, Optional

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer
from prompt_toolkit.history import FileHistory, History
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, Window, FloatContainer, Float
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea, Frame

from . import renderer


class InputController:
    def __init__(
        self,
        *,
        history: Optional[History] = None,
        completer: Optional[Completer] = None,
        style: Optional[Style] = None,
        mode_line_fn: Callable[[], str] = lambda: "",
        prompt_symbol: str = "❯ ",
    ):
        self._history = history
        self._completer = completer
        self._style = style
        self._mode_line_fn = mode_line_fn
        self._prompt_symbol = prompt_symbol

        self._q: "_queue.Queue[Optional[str]]" = _queue.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._patch_cm = None

        self.pending: list[str] = []
        self._interrupt_cb: Optional[Callable[[], None]] = None
        self._busy = False

        self._paused = threading.Event()
        self._resumed = threading.Event()
        self._resumed.set()

        self._app: Optional[Application] = None
        # NOTE: do NOT build the Application here — constructing it touches the
        # terminal output (Win32 console buffer) and raises in non-console
        # contexts. Build lazily in start(), which runs inside the real REPL.

    # ── application construction ──────────────────────────────────────────────

    def _build_app(self):
        kb = KeyBindings()

        @kb.add("enter")
        def _submit(event):
            # Submit unless the user is composing a continuation (handled by c-j).
            buf = self.textarea.buffer
            event.app.exit(result=buf.text)

        @kb.add("c-j")
        def _newline(event):
            self.textarea.buffer.insert_text("\n")

        @kb.add("c-c")
        @kb.add("c-d")
        def _eof(event):
            event.app.exit(result=None, exception=EOFError)

        @kb.add("escape", eager=True)
        def _interrupt(event):
            if self.textarea.buffer.text.strip():
                return  # don't interrupt while composing
            self.fire_interrupt()

        self.textarea = TextArea(
            height=None,
            prompt=self._prompt_symbol,
            multiline=True,
            wrap_lines=True,
            history=self._history,
            completer=self._completer,
            complete_while_typing=True,
            focus_on_click=True,
            scrollbar=False,
        )

        # mode line below the box, e.g. "  ⏵⏵ agent · opus 4.7 · 1 queued"
        def _mode_text():
            return [("class:modeline", "  " + self._mode_line_fn())]

        body = HSplit([
            Frame(self.textarea),
            Window(FormattedTextControl(_mode_text), height=1, always_hide_cursor=True),
        ])

        # FloatContainer lets the completion menu pop up over the input box.
        root = FloatContainer(
            content=body,
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(max_height=12, scroll_offset=1),
                ),
            ],
        )

        base_style = {
            "frame.border": "fg:#505050",
            "modeline": "fg:#888888",
            "completion-menu.completion": "bg:#1c1c1c fg:#bbbbbb",
            "completion-menu.completion.current": "bg:#b1b9f9 fg:#000000",
            "completion-menu.meta.completion": "bg:#1c1c1c fg:#777777",
            "completion-menu.meta.completion.current": "bg:#8a93cc fg:#000000",
        }
        merged = Style.from_dict(base_style)
        if self._style is not None:
            merged = merge_styles_safe(self._style, merged)

        self._app = Application(
            layout=Layout(root, focused_element=self.textarea),
            key_bindings=kb,
            style=merged,
            full_screen=False,
            mouse_support=False,
            erase_when_done=True,
            # repaint periodically so the mode line (queued/running) stays fresh
            # and terminal-resize artifacts get cleared on the next tick.
            refresh_interval=0.5,
        )

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        if self._app is None:
            self._build_app()
        self._patch_cm = patch_stdout(raw=True)
        self._patch_cm.__enter__()
        import sys
        renderer.console.file = sys.stdout
        self._thread = threading.Thread(target=self._run, name="deepcode-input", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._q.put(None)
        try:
            if self._app is not None and self._app.is_running:
                self._app.exit(result=None, exception=EOFError)
        except Exception:
            pass
        if self._patch_cm is not None:
            try:
                self._patch_cm.__exit__(None, None, None)
            except Exception:
                pass
            self._patch_cm = None
            renderer.console.file = renderer._real_console_file

    # ── interrupt wiring ──────────────────────────────────────────────────────

    def set_interrupt(self, cb: Optional[Callable[[], None]]):
        self._interrupt_cb = cb
        self._busy = cb is not None
        self._invalidate()

    def fire_interrupt(self):
        cb = self._interrupt_cb
        if cb is None or self._loop is None:
            return
        self._loop.call_soon_threadsafe(cb)

    def _invalidate(self):
        try:
            if self._app is not None and self._app.is_running:
                self._app.invalidate()
        except Exception:
            pass

    # ── input thread ──────────────────────────────────────────────────────────

    def _run(self):
        while not self._stop.is_set():
            if self._paused.is_set():
                self._resumed.set()
                while self._paused.is_set() and not self._stop.is_set():
                    threading.Event().wait(0.03)
                self._resumed.clear()
                continue
            # reset the box for a fresh line
            self.textarea.buffer.reset()
            try:
                line = self._app.run()
            except EOFError:
                self._q.put(None)
                return
            except (KeyboardInterrupt,):
                self._q.put(None)
                return
            except Exception:
                continue
            if self._stop.is_set():
                return
            if self._paused.is_set():
                continue
            if line is None:
                self._q.put(None)
                return
            self._q.put(line)

    # ── pause/resume for blocking pickers ─────────────────────────────────────

    def pause(self):
        self._paused.set()
        try:
            if self._app is not None and self._app.is_running:
                self._app.exit(result="")
        except Exception:
            pass
        for _ in range(80):
            if self._resumed.is_set() and not self._reading():
                break
            threading.Event().wait(0.01)

    def resume(self):
        self._paused.clear()

    def _reading(self) -> bool:
        try:
            return bool(self._app is not None and self._app.is_running)
        except Exception:
            return False

    # ── async consumption ─────────────────────────────────────────────────────

    async def get_line(self) -> Optional[str]:
        assert self._loop is not None
        while True:
            try:
                return self._q.get_nowait()
            except _queue.Empty:
                await asyncio.sleep(0.02)

    async def read_one(self) -> Optional[str]:
        return await self.get_line()


def merge_styles_safe(a: Style, b: Style) -> Style:
    from prompt_toolkit.styles import merge_styles
    try:
        return merge_styles([a, b])
    except Exception:
        return b
