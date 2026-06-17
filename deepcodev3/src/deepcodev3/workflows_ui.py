"""
Full-screen /workflows TUI — view running/recent UltraCode swarms.

Layout (matches claude-code's workflow viewer):
  - Swarm picker (if >1 swarm): list of active/recent swarms, enter to open.
  - Phases pane (left): groups in the swarm, arrow keys to navigate.
  - Agents pane (right): workers of the selected group, enter to focus,
    arrow keys to navigate, enter again to view an agent's activity log.
  - Esc steps back one level; esc at the top level exits.
  - 'i' opens a prompt to inject a live note into the running swarm.
"""
from __future__ import annotations

import asyncio
import time

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style

from .ultracode import ACTIVE_SWARMS, Status, SwarmState

STYLE = Style.from_dict({
    "header": "bold fg:#d77757",
    "title": "bold",
    "dim": "fg:#808080",
    "selected": "reverse",
    "done": "fg:#4eba65",
    "running": "fg:#ffc107",
    "pending": "fg:#808080",
    "failed": "fg:#ff6b80",
    "pane-title": "bold fg:#b1b9f9",
    "footer": "fg:#808080",
    "current": "bold fg:#ffd60a",
    "empty": "fg:#ffd60a",
})

_STATUS_ICON = {
    Status.DONE: ("✓", "done"),
    Status.RUNNING: ("●", "running"),
    Status.PENDING: ("○", "pending"),
    Status.FAILED: ("✗", "failed"),
    Status.BLOCKED: ("○", "pending"),
}


class WorkflowsApp:
    LEVEL_SWARMS = 0
    LEVEL_PHASES = 1
    LEVEL_AGENTS = 2
    LEVEL_LOG = 3

    def __init__(self):
        self.level = self.LEVEL_SWARMS
        self.swarm_idx = 0
        self.phase_idx = 0
        self.agent_idx = 0
        self.note_mode = False
        self.note_buffer = ""

    # ── data helpers ────────────────────────────────────────────────────────

    @property
    def swarms(self) -> list[SwarmState]:
        return ACTIVE_SWARMS

    @property
    def swarm(self) -> SwarmState | None:
        if not self.swarms:
            return None
        self.swarm_idx = max(0, min(self.swarm_idx, len(self.swarms) - 1))
        return self.swarms[self.swarm_idx]

    @property
    def phases(self):
        s = self.swarm
        return s.plans if s else []

    @property
    def current_phase(self):
        phases = self.phases
        if not phases:
            return None
        self.phase_idx = max(0, min(self.phase_idx, len(phases) - 1))
        return phases[self.phase_idx]

    @property
    def agents(self):
        s = self.swarm
        p = self.current_phase
        if not s or not p:
            return []
        return sorted(s.agents_for_group(p.id), key=lambda a: a.agent_id)

    @property
    def current_agent(self):
        agents = self.agents
        if not agents:
            return None
        self.agent_idx = max(0, min(self.agent_idx, len(agents) - 1))
        return agents[self.agent_idx]

    # ── rendering ───────────────────────────────────────────────────────────

    def render_header(self):
        s = self.swarm
        if not s:
            return [("class:dim", "  No UltraCode swarms running yet. Use /ultracode or say \"ultracode ...\" to start one.")]
        elapsed = time.time() - s.started_at
        n_agents = len(s.agents)
        done_agents = sum(1 for a in s.agents.values() if a.status == Status.DONE)
        status_word = "failed" if s.failed else ("done" if s.finished else "running")
        title_style = "class:title"
        meta_style = "class:dim"
        if self.level == self.LEVEL_SWARMS:
            title_style = "class:selected " + title_style
            meta_style = "class:selected " + meta_style
        out = [
            (title_style, f"  {s.task[:90]}\n"),
            (meta_style, f"  {done_agents}/{n_agents} agents · {elapsed:.0f}s · {status_word}\n"),
        ]
        return out

    def render_phases(self):
        s = self.swarm
        lines = []
        if not s:
            return [("class:dim", "")]
        for i, p in enumerate(self.phases):
            status = s.group_status.get(p.id, Status.PENDING)
            icon, cls = _STATUS_ICON.get(status, ("○", "pending"))
            n = len(s.agents_for_group(p.id))
            done = sum(1 for a in s.agents_for_group(p.id) if a.status == Status.DONE)
            line = f"{icon} {i+1} {p.role}  {done}/{n}\n" if n else f"{icon} {i+1} {p.role}\n"
            # Highlight the currently-running phase in yellow regardless of selection.
            style = "class:current" if status == Status.RUNNING else f"class:{cls}"
            if self.level >= self.LEVEL_PHASES and i == self.phase_idx:
                style = "class:selected " + style
            lines.append((style, line))
        return lines or [("class:dim", "  (no phases yet)")]

    def render_agents(self):
        p = self.current_phase
        if not p:
            return [("class:dim", "  Select a phase")]
        agents = self.agents
        if not agents:
            empty_style = "class:empty"
            if self.level >= self.LEVEL_AGENTS:
                empty_style = "class:selected " + empty_style
            return [
                ("class:pane-title", f"  {p.role} · 0 agents\n\n"),
                (empty_style, "  nothing here yet 😴\n"),
            ]
        lines = [("class:pane-title", f"  {p.role} · {len(agents)} agents\n")]
        for i, a in enumerate(self.agents):
            icon, cls = _STATUS_ICON.get(a.status, ("○", "pending"))
            name = a.name or a.agent_id
            rblock = f"🧠{a.reasoning} · " if getattr(a, "reasoning", "off") != "off" else ""
            meta = f"{rblock}{a.tokens//1000}k tok · {a.tool_calls} tools · {a.elapsed:.0f}s"
            line = f"{icon} {a.agent_id}  {name[:50]}\n      {a.action[:60]}  ·  {meta}\n"
            style = f"class:{cls}"
            if self.level >= self.LEVEL_AGENTS and i == self.agent_idx:
                style = "class:selected " + style
            lines.append((style, line))
        return lines

    def render_log(self):
        a = self.current_agent
        if not a:
            return [("class:dim", "  Select an agent")]
        lines = [("class:pane-title", f"  {a.agent_id} — {a.name[:60]}\n"), ("class:dim", f"  model: {a.model_id}\n\n")]
        for entry in a.log[-200:]:
            for ln in str(entry).splitlines() or [""]:
                lines.append(("", f"  {ln}\n"))
        return lines or [("class:dim", "  (no activity yet)")]

    def render_swarm_list(self):
        if not self.swarms:
            return [("class:dim", "  No swarms yet.")]
        lines = [("class:pane-title", "  UltraCode workflows\n\n")]
        for i, s in enumerate(self.swarms):
            status_word = "failed" if s.failed else ("done" if s.finished else "running")
            cls = "failed" if s.failed else ("done" if s.finished else "running")
            style = f"class:{cls}"
            if self.level == self.LEVEL_SWARMS and i == self.swarm_idx:
                style = "class:selected " + style
            elapsed = time.time() - s.started_at
            lines.append((style, f"  {s.task[:70]}  ·  {status_word} · {elapsed:.0f}s\n"))
        return lines

    def render_footer(self):
        if self.note_mode:
            return [("class:footer", f"  inject note > {self.note_buffer}")]
        hints = {
            self.LEVEL_SWARMS: "↑↓ select · enter open · esc exit",
            self.LEVEL_PHASES: "↑↓ select phase · enter agents · esc back",
            self.LEVEL_AGENTS: "↑↓ select agent · enter view log · i inject note · esc back",
            self.LEVEL_LOG: "↑↓ scroll · esc back",
        }
        return [("class:footer", "  " + hints.get(self.level, ""))]

    # ── navigation ──────────────────────────────────────────────────────────

    def move(self, delta: int):
        if self.level == self.LEVEL_SWARMS:
            self.swarm_idx += delta
        elif self.level == self.LEVEL_PHASES:
            self.phase_idx += delta
        elif self.level == self.LEVEL_AGENTS:
            self.agent_idx += delta

    def enter(self):
        if self.level == self.LEVEL_SWARMS:
            if self.swarms:
                self.level = self.LEVEL_PHASES
                self.phase_idx = 0
        elif self.level == self.LEVEL_PHASES:
            if self.phases:
                self.level = self.LEVEL_AGENTS
                self.agent_idx = 0
        elif self.level == self.LEVEL_AGENTS:
            if self.agents:
                self.level = self.LEVEL_LOG

    def back(self) -> bool:
        """Returns False if at top level (caller should exit)."""
        if self.level == self.LEVEL_LOG:
            self.level = self.LEVEL_AGENTS
        elif self.level == self.LEVEL_AGENTS:
            self.level = self.LEVEL_PHASES
        elif self.level == self.LEVEL_PHASES:
            self.level = self.LEVEL_SWARMS
        else:
            return False
        return True


def run_workflows_ui():
    app_state = WorkflowsApp()
    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _(event):
        if app_state.note_mode:
            return
        app_state.move(-1)

    @kb.add("down")
    @kb.add("j")
    def _(event):
        if app_state.note_mode:
            return
        app_state.move(1)

    @kb.add("enter")
    def _(event):
        if app_state.note_mode:
            s = app_state.swarm
            note = app_state.note_buffer.strip()
            if s and note:
                s.extra_notes.append(note)
            app_state.note_mode = False
            app_state.note_buffer = ""
            return
        app_state.enter()

    @kb.add("right")
    def _(event):
        if app_state.note_mode:
            return
        app_state.enter()

    @kb.add("escape")
    @kb.add("left")
    @kb.add("q")
    def _(event):
        if app_state.note_mode:
            app_state.note_mode = False
            app_state.note_buffer = ""
            return
        if not app_state.back():
            event.app.exit()

    @kb.add("i")
    def _(event):
        if app_state.level == app_state.LEVEL_AGENTS and not app_state.note_mode:
            app_state.note_mode = True
            app_state.note_buffer = ""

    @kb.add("c-c")
    def _(event):
        event.app.exit()

    @kb.add("<any>")
    def _(event):
        if app_state.note_mode:
            ch = event.data
            if ch == "\x7f" or ch == "\x08":  # backspace
                app_state.note_buffer = app_state.note_buffer[:-1]
            elif ch and ch.isprintable():
                app_state.note_buffer += ch

    swarm_picker = Window(content=FormattedTextControl(app_state.render_swarm_list), wrap_lines=False)
    header = Window(content=FormattedTextControl(app_state.render_header), height=D(min=2, max=2), wrap_lines=False)
    phases = Window(content=FormattedTextControl(app_state.render_phases), width=D(weight=1), wrap_lines=False)
    agents_or_log = Window(
        content=FormattedTextControl(lambda: app_state.render_log() if app_state.level == app_state.LEVEL_LOG else app_state.render_agents()),
        width=D(weight=2),
        wrap_lines=True,
    )
    footer = Window(content=FormattedTextControl(app_state.render_footer), height=D(min=1, max=1), wrap_lines=False)

    body = HSplit([
        header,
        Window(height=1, char="─"),
        VSplit([phases, Window(width=1, char="│"), agents_or_log]),
        Window(height=1, char="─"),
        footer,
    ])

    layout = Layout(body)

    async def _refresh_loop(app: Application):
        while True:
            await asyncio.sleep(0.5)
            if app.is_done:
                break
            app.invalidate()

    app_kwargs = dict(layout=layout, key_bindings=kb, full_screen=True, style=STYLE, mouse_support=False)
    app = Application(**app_kwargs)

    async def _run():
        from . import renderer
        refresher = asyncio.get_event_loop().create_task(_refresh_loop(app))
        renderer.set_tui_active(True)
        try:
            await app.run_async()
        except Exception as e:
            renderer.set_tui_active(False)
            renderer.print_error(f"/workflows UI failed: {e}")
        finally:
            renderer.set_tui_active(False)
            refresher.cancel()

    return _run()
