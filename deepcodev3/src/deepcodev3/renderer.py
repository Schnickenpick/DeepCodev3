# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich.padding import Padding
from rich import box
from .models import PROVIDERS, TIER_COLORS, get_model

console = Console(highlight=False)

_stream_at_line_start = True
_streamed_lines = 0
_notify = True

# While a full-screen TUI (e.g. /workflows) owns the terminal, background
# tasks (UltraCode swarms) must not write to stdout — it corrupts the
# alt-screen buffer. Route console output to a black hole instead.
_real_console_file = console.file
_tui_active = False


class _BufferFile:
    """Captures writes while a full-screen TUI is active, so output isn't lost —
    just deferred until the TUI closes and we can flush it to the real console."""
    def __init__(self):
        self.buf = []

    def write(self, s):
        if s:
            self.buf.append(s)

    def flush(self):
        pass

    def isatty(self):
        return False


_buffer_file = _BufferFile()


def set_tui_active(active: bool):
    """Suppress/restore renderer.console output while a full-screen TUI is open.
    Output written while suppressed is buffered and flushed to the real
    terminal once the TUI closes (so swarm completion messages aren't lost)."""
    global _tui_active
    _tui_active = active
    if active:
        console.file = _buffer_file
    else:
        console.file = _real_console_file
        # Force the terminal cursor onto a fresh line before anything else
        # writes — prompt_toolkit's full-screen app may leave the cursor
        # mid-line when it restores the normal screen buffer.
        _real_console_file.write("\n")
        if _buffer_file.buf:
            _real_console_file.write("".join(_buffer_file.buf))
            _buffer_file.buf.clear()
        _real_console_file.flush()


def is_tui_active() -> bool:
    return _tui_active

# Claude-code color palette (dark theme, from src/utils/theme.ts)
CLAUDE_ORANGE = "rgb(215,119,87)"
SUCCESS_GREEN = "rgb(78,186,101)"
ERROR_RED     = "rgb(255,107,128)"
WARNING_AMBER = "rgb(255,193,7)"
SUBTLE_GRAY   = "rgb(80,80,80)"
PERMISSION_BLUE = "rgb(177,185,249)"
DIFF_ADD_BG   = "rgb(34,92,43)"
DIFF_DEL_BG   = "rgb(122,41,54)"

BLACK_CIRCLE = "●"


def set_notify(enabled: bool):
    global _notify
    _notify = enabled


CLAUDE_LOGO = [
    "  ▐▛███▜▌  ",
    " ▝▜█████▛▘ ",
    "   ▘▘ ▝▝   ",
]


def print_banner(model_id: str | None = None, username: str | None = None):
    from .models import get_model
    from . import __version__
    import os

    name = username or os.environ.get("USERNAME") or os.environ.get("USER") or "there"
    model_line = ""
    if model_id:
        m = get_model(model_id)
        model_line = f"{m['name']}"

    cwd = str(Path.cwd())
    home = str(Path.home())
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]

    left_lines = [
        "",
        f"Welcome back, [bold]{name}[/bold]!",
        "",
        *[f"[{CLAUDE_ORANGE}]{l}[/{CLAUDE_ORANGE}]" for l in CLAUDE_LOGO],
        "",
    ]
    if model_line:
        left_lines.append(f"[dim]{model_line}[/dim]")
    left_lines.append(f"[dim]{cwd}[/dim]")

    right_lines = [
        "[bold]Tips for getting started[/bold]",
        "Run [bold]/init[/bold] for a DEEPCODE.md",
        "Run [bold]/agent[/bold] to enable tools",
        "Run [bold]/permissions[/bold] for rules",
        "[dim]" + "─" * 34 + "[/dim]",
        "[bold]What's new[/bold]",
        "claude-code-style tools + diffs",
        "+ permission engine",
        "34 models · /models to browse",
        "/plan for step-by-step execution",
    ]

    left = Text.from_markup("\n".join(left_lines), justify="center")
    right = Text.from_markup("\n".join(right_lines))

    from rich.table import Table
    grid = Table.grid(expand=False, padding=(0, 2))
    grid.add_column(width=28)
    grid.add_column(width=38)
    grid.add_row(left, right)

    console.print()
    console.print(Padding(
        Panel(
            grid,
            title=f"[{CLAUDE_ORANGE} bold]DeepCode v3[/{CLAUDE_ORANGE} bold]",
            border_style=SUBTLE_GRAY,
            box=box.ROUNDED,
            padding=(0, 1),
        ),
        pad=(0, 0, 0, 1),
    ))
    console.print()


def print_model_status(model_id: str, mode: str = "chat", agent: bool = False):
    m = get_model(model_id)
    provider = PROVIDERS.get(m["provider"], {})
    color = provider.get("color", "white")
    tier_color = TIER_COLORS.get(m["tier"], "white")

    if mode == "merge":
        line = "[yellow bold]⚡ Merge AI[/yellow bold]  [dim]GPT-5.5 + Claude Opus 4.7 + Gemini 2.5 Pro[/dim]"
    elif mode == "search":
        line = "[blue bold]🔍 Web Search[/blue bold]  [dim]Searches web, synthesizes with sources[/dim]"
    else:
        line = f"[{color} bold]{m['name']}[/{color} bold]  [dim]{provider.get('name','?')} · [{tier_color}]{m['tier']}[/{tier_color}][/dim]"

    agent_tag = f"  [bold {SUCCESS_GREEN}]{BLACK_CIRCLE} agent[/bold {SUCCESS_GREEN}]" if agent else ""
    console.print(f"  {line}{agent_tag}")
    console.print(f"  [dim]Type [{PERMISSION_BLUE}]/help[/{PERMISSION_BLUE}] for commands[/dim]")
    console.print()


def print_help(agent: bool = False, commands=None):
    rows = commands or []
    # Patch agent status into description dynamically
    patched = []
    for cmd, desc in rows:
        if cmd == "/agent":
            desc = f"Toggle agent mode (tools) — currently {'[green]ON[/green]' if agent else '[dim]OFF[/dim]'}"
        patched.append((cmd, desc))
    console.print()
    col = max((len(cmd) for cmd, _ in patched), default=10) + 2
    for cmd, desc in patched:
        spaces = " " * (col - len(cmd))
        console.print(f"  [bold cyan]{cmd}[/bold cyan]{spaces}{desc}")
    console.print()


def print_models_list(current_model_id: str):
    from .models import MODELS
    console.print()
    current_provider = None
    for m in MODELS:
        if m["provider"] != current_provider:
            current_provider = m["provider"]
            p = PROVIDERS.get(current_provider, {})
            console.print(f"  [{p.get('color','white')} bold]{p.get('name','?')}[/{p.get('color','white')} bold]")
        active = " ◀" if m["id"] == current_model_id else ""
        tier_color = TIER_COLORS.get(m["tier"], "white")
        console.print(f"    [dim]{m['name']:<26}[/dim][{tier_color}]{m['tier']:<12}[/{tier_color}][green]{active}[/green]")
    console.print()


def print_user_label(text: str):
    console.print()
    console.print(f"  [bold white]You[/bold white]")
    console.print(f"  [white]{text}[/white]")
    console.print()


def print_assistant_header(model_id: str, mode: str = "chat"):
    global _stream_at_line_start, _streamed_lines
    _stream_at_line_start = True
    _streamed_lines = 0

    if mode == "merge":
        label = "⚡ Merge AI"
    elif mode == "search":
        label = "🔍 Web Search"
    else:
        m = get_model(model_id)
        label = m["name"]

    console.print(f"  [{CLAUDE_ORANGE} bold]{BLACK_CIRCLE}[/{CLAUDE_ORANGE} bold] [dim]{label}[/dim]")


def stream_token(token: str):
    """No-op during streaming — we buffer and render after."""
    pass


def finish_stream(full_text: str):
    global _stream_at_line_start, _streamed_lines
    _stream_at_line_start = True
    _streamed_lines = 0

    text = full_text.strip()
    if not text:
        return

    for line in text.splitlines():
        console.print("  " + line, markup=False, highlight=False)
    console.print()
    if _notify:
        print("\a", end="", flush=True)


def print_reasoning(text: str):
    if not text:
        return
    console.print(Padding(
        Panel(
            Markdown(text),
            title="[dim]Reasoning[/dim]",
            border_style="dim magenta",
            padding=(0, 1),
        ),
        pad=(0, 0, 0, 2),
    ))


def print_search_sources(sources: list):
    if not sources:
        return
    console.print("  [blue bold]Sources[/blue bold]")
    for s in sources:
        console.print(f"  [dim]·[/dim] {s.get('title', s.get('url',''))}")
    console.print()


def print_status(msg: str):
    console.print(f"  [dim yellow]{msg}[/dim yellow]", end="\r")


def print_response_time(elapsed: float):
    console.print(f"  [dim]⏱ {elapsed:.1f}s[/dim]")


def print_keybinds():
    rows = [
        ("Enter",       "Send message"),
        ("Ctrl+J",      "New line (multiline input)"),
        ("Tab",         "Autocomplete command / cycle options"),
        ("↑ / ↓",       "Navigate autocomplete menu / history"),
        ("Ctrl+C",      "Cancel / exit"),
        ("Ctrl+R",      "Search input history"),
    ]
    console.print()
    console.print("  [bold]Keybinds[/bold]")
    for key, desc in rows:
        console.print(f"  [bold cyan]{key:<20}[/bold cyan] [dim]{desc}[/dim]")
    console.print()


def print_error(msg: str):
    console.print(f"\n  [bold {ERROR_RED}]✗[/bold {ERROR_RED}] {msg}\n")


def print_info(msg: str):
    console.print(f"  [dim]{msg}[/dim]")


def render_markdown(text: str):
    console.print(Padding(Markdown(text), pad=(0, 0, 0, 2)))


def print_memory(memory_md: str, user_md: str = "", project_memory_md: str = ""):
    any_content = any([memory_md.strip(), user_md.strip(), project_memory_md.strip()])
    if not any_content:
        console.print("\n  [dim]No memories yet.[/dim]\n")
        return
    console.print()
    if user_md.strip():
        console.print("  [bold cyan]User[/bold cyan]")
        for line in user_md.strip().splitlines():
            console.print(f"  [dim]{line}[/dim]")
        console.print()
    if memory_md.strip():
        console.print("  [bold cyan]Global[/bold cyan]")
        for line in memory_md.strip().splitlines():
            console.print(f"  [dim]{line}[/dim]")
        console.print()
    if project_memory_md.strip():
        console.print("  [bold cyan]Project[/bold cyan]")
        for line in project_memory_md.strip().splitlines():
            console.print(f"  [dim]{line}[/dim]")
        console.print()


def print_quiz(options: list[str]) -> None:
    """Render numbered quiz options. Last option is always 'Type something different'."""
    for i, opt in enumerate(options, 1):
        if i == len(options):
            console.print(f"  [dim]{i}. {opt}[/dim]")
        else:
            console.print(f"  [bold {PERMISSION_BLUE}]{i}.[/bold {PERMISSION_BLUE}] {opt}")
    console.print()


def print_todos(todos: list[dict]) -> None:
    """Render a todo checklist: [{"content": str, "status": "pending"|"in_progress"|"completed"}]."""
    if not todos:
        return
    console.print()
    console.print("  [bold]Todos[/bold]")
    for t in todos:
        status = t.get("status", "pending")
        content = t.get("content", "")
        if status == "completed":
            console.print(f"  [{SUCCESS_GREEN}]✓[/{SUCCESS_GREEN}] [dim strike]{content}[/dim strike]")
        elif status == "in_progress":
            console.print(f"  [{WARNING_AMBER}]●[/{WARNING_AMBER}] [bold]{content}[/bold]")
        else:
            console.print(f"  [dim]○ {content}[/dim]")
    console.print()


def print_permission_rules(rules: list[dict]) -> None:
    """Render persisted permission rules with their index for /permissions removal."""
    if not rules:
        console.print("\n  [dim]No saved permission rules.[/dim]\n")
        return
    console.print()
    console.print("  [bold]Permission rules[/bold]  [dim](/permissions remove <n>)[/dim]")
    for i, r in enumerate(rules):
        decision = r.get("decision", "")
        color = "green" if decision == "allow" else "red"
        tool = r.get("tool", "*")
        pattern = r.get("pattern") or "*"
        console.print(f"  {i}. [{color}]{decision:<5}[/{color}] [bold]{tool}[/bold]  [dim]{pattern}[/dim]")
    console.print()
