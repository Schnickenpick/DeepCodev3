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
        mode_cycle_cb: Optional[Callable[[], None]] = None,
        prompt_symbol: str = "❯ ",
        accent: str = "#d77757",
    ):
        self._history = history
        self._completer = completer
        self._style = style
        self._mode_line_fn = mode_line_fn
        self._mode_cycle_cb = mode_cycle_cb
        self._prompt_symbol = prompt_symbol
        self._accent = accent  # #rrggbb — frame border + prompt symbol color

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._eof = threading.Event()
        self._patch_cm = None

        # Single source of truth for submitted-but-unprocessed messages.
        # The input thread appends; the async loop pops from the front; the
        # Up-arrow binding pops from the back to edit. Guarded by _plock.
        self.pending: list[str] = []
        self._plock = threading.Lock()
        self._interrupt_cb: Optional[Callable[[], None]] = None
        self._busy = False
        # live turn stats for the status line (set by the dispatch via
        # begin_turn / update_turn / end_turn)
        self._turn_start: Optional[float] = None
        self._turn_tokens: int = 0

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

        @kb.add("backspace")
        def _backspace(event):
            # On an empty box, swallow backspace so the terminal bell doesn't
            # ring (prompt_toolkit's default delete-before-cursor beeps when
            # there's nothing to delete). Otherwise delete normally.
            buf = self.textarea.buffer
            if buf.cursor_position == 0:
                return
            buf.delete_before_cursor(count=event.arg)

        @kb.add("s-tab")  # shift+tab: cycle permission mode
        def _cycle_mode(event):
            if self._mode_cycle_cb is not None:
                self._mode_cycle_cb()
            event.app.invalidate()

        @kb.add("up")
        def _up(event):
            buf = self.textarea.buffer
            # If the box is empty and there are queued messages, pull the most
            # recent queued one back into the box for editing (and unqueue it).
            if not buf.text and self._has_pending():
                msg = self._pop_last_pending()
                if msg is not None:
                    buf.text = msg
                    buf.cursor_position = len(msg)
                return
            # Otherwise normal multiline / history up-navigation.
            if buf.document.cursor_position_row > 0:
                buf.cursor_up()
            else:
                buf.history_backward()

        @kb.add("down")
        def _down(event):
            buf = self.textarea.buffer
            if buf.document.cursor_position_row < buf.document.line_count - 1:
                buf.cursor_down()
            else:
                buf.history_forward()

        from prompt_toolkit.layout.dimension import Dimension
        # Height EXACTLY tracks the input's wrapped line count: 1 line by
        # default, growing downward as the user adds lines (capped at 10 so a
        # huge paste can't eat the screen). No slack — preferred==max==content
        # so the box never reserves extra rows. The Frame adds the border.
        def _input_height():
            rows = self.textarea.buffer.document.line_count
            rows = max(1, min(rows, 10))
            return Dimension(min=1, preferred=rows, max=rows)

        self.textarea = TextArea(
            height=_input_height,
            prompt=[("class:prompt-symbol", self._prompt_symbol)],
            multiline=True,
            wrap_lines=True,
            history=self._history,
            completer=self._completer,
            complete_while_typing=True,
            focus_on_click=True,
            scrollbar=False,
        )

        # queued messages shown dim ABOVE the box (Claude-Code style). Press Up
        # on an empty box to pull the most recent one back for editing.
        def _queued_text():
            with self._plock:
                items = list(self.pending)
            if not items:
                return []
            out = []
            for m in items:
                first = m.splitlines()[0] if m else m
                if len(first) > 80:
                    first = first[:77] + "…"
                out.append(("class:queued", f"  ❯ {first}\n"))
            return out

        def _queued_height():
            with self._plock:
                return len(self.pending)

        # mode line below the box, e.g. "  ⏵⏵ agent · opus 4.7 · 1 queued"
        def _mode_text():
            return [("class:modeline", "  " + self._mode_line_fn())]

        body = HSplit([
            Window(FormattedTextControl(_queued_text), height=_queued_height,
                   always_hide_cursor=True),
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

        self._app = Application(
            layout=Layout(root, focused_element=self.textarea),
            key_bindings=kb,
            style=self._make_style(),
            full_screen=False,
            mouse_support=False,
            erase_when_done=True,
            # repaint periodically so the mode line (queued/running) stays fresh
            # and terminal-resize artifacts get cleared on the next tick.
            refresh_interval=0.5,
        )

    def _make_style(self) -> Style:
        """Build the Application style. Frame border + prompt symbol use the
        live accent so /color recolors the input box."""
        base_style = {
            "frame.border": f"fg:{self._accent}",
            "prompt-symbol": f"fg:{self._accent} bold",
            "modeline": "fg:#888888",
            "queued": "fg:#666666 italic",
            "completion-menu.completion": "bg:#1c1c1c fg:#bbbbbb",
            "completion-menu.completion.current": f"bg:{self._accent} fg:#000000",
            "completion-menu.meta.completion": "bg:#1c1c1c fg:#777777",
            "completion-menu.meta.completion.current": "bg:#8a93cc fg:#000000",
        }
        merged = Style.from_dict(base_style)
        if self._style is not None:
            merged = merge_styles_safe(self._style, merged)
        return merged

    def set_accent(self, accent_hex: str):
        """Recolor the input box at runtime (/color). `accent_hex` is #rrggbb."""
        self._accent = accent_hex
        if self._app is not None:
            self._app.style = self._make_style()
            try:
                self._app.invalidate()
            except Exception:
                pass

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        if self._app is None:
            self._build_app()
        self._patch_cm = patch_stdout(raw=True)
        self._patch_cm.__enter__()
        import sys
        renderer.console.file = sys.stdout
        renderer.set_bottom_bar_active(True)
        self._thread = threading.Thread(target=self._run, name="deepcode-input", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._eof.set()
        renderer.set_bottom_bar_active(False)
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

    # ── live turn status (for the mode line) ──────────────────────────────────

    def begin_turn(self):
        import time
        self._turn_start = time.time()
        self._turn_tokens = 0

    def update_turn(self, tokens: int):
        self._turn_tokens = tokens

    def end_turn(self):
        self._turn_start = None
        self._turn_tokens = 0

    def status_suffix(self) -> str:
        """Live '· 1.2k tok · 8s · Esc to interrupt' shown while a turn runs."""
        if self._turn_start is None:
            return ""
        import time
        elapsed = time.time() - self._turn_start
        tok = self._turn_tokens
        tok_s = f"{tok/1000:.1f}k" if tok >= 1000 else str(tok)
        return f"  ·  {tok_s} tok  ·  {elapsed:.0f}s  ·  Esc to interrupt"

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
            except (EOFError, KeyboardInterrupt):
                self._eof.set()
                return
            except Exception:
                continue
            if self._stop.is_set():
                return
            if self._paused.is_set():
                continue
            if line is None:
                self._eof.set()
                return
            line = line.strip()
            if not line:
                continue
            with self._plock:
                self.pending.append(line)
            self._invalidate()

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

    # ── pending queue helpers (thread-safe) ───────────────────────────────────

    def _has_pending(self) -> bool:
        with self._plock:
            return bool(self.pending)

    def _pop_last_pending(self) -> Optional[str]:
        """Up-arrow: take the most recently queued message back for editing."""
        with self._plock:
            if self.pending:
                return self.pending.pop()
        return None

    def _pop_first_pending(self) -> Optional[str]:
        with self._plock:
            if self.pending:
                return self.pending.pop(0)
        return None

    # ── async consumption ─────────────────────────────────────────────────────

    async def get_line(self) -> Optional[str]:
        """Await the next message: FIFO from the pending queue. Returns None on
        EOF/shutdown (only once the queue is drained)."""
        assert self._loop is not None
        while True:
            msg = self._pop_first_pending()
            if msg is not None:
                self._invalidate()
                return msg
            if self._eof.is_set() or self._stop.is_set():
                return None
            await asyncio.sleep(0.02)

    async def read_one(self) -> Optional[str]:
        return await self.get_line()


def merge_styles_safe(a: Style, b: Style) -> Style:
    from prompt_toolkit.styles import merge_styles
    try:
        return merge_styles([a, b])
    except Exception:
        return b
