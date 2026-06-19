from __future__ import annotations
import asyncio
import json
import re
import sys
import time
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.keys import Keys
from prompt_toolkit.filters import is_done
from prompt_toolkit.key_binding import KeyBindings

from . import api, renderer, storage, permissions
from .models import DEFAULT_MODEL, find_model, MODELS
from .agent import run_agent
from .system_prompt import SYSTEM_PROMPT
from .reasoning import run_reasoning, LEVELS as REASONING_LEVELS
from .input_loop import InputController

from .permissions import _getch

QUIZ_RE = re.compile(r'<quiz>([\s\S]*?)</quiz>')
DEFAULT_QUIZ_MAX = 5


async def _run_cancelable(coro):
    """Run `coro` as a task that the user can interrupt by pressing Esc (on an
    empty input line) while it runs. The persistent input controller owns the
    keyboard now, so we register this task's cancel as the controller's
    interrupt callback rather than polling msvcrt ourselves. Returns the coro's
    result, or None if interrupted."""
    task = asyncio.ensure_future(coro)
    ctrl = _INPUT_CONTROLLER
    if ctrl is None:
        # no controller (shouldn't happen in normal REPL) — plain await
        return await task

    ctrl.set_interrupt(task.cancel)
    ctrl.begin_turn()
    try:
        result = await task
    except asyncio.CancelledError:
        renderer.print_info("Interrupted.")
        return None
    finally:
        ctrl.set_interrupt(None)
        ctrl.end_turn()
    return result


def _parse_quiz(text: str, max_options: int) -> tuple[str, dict | None]:
    """Extract quiz block. Returns (clean_text, quiz_data) or (text, None)."""
    m = QUIZ_RE.search(text)
    if not m:
        return text, None
    try:
        data = json.loads(m.group(1).strip())
        options = data.get("options", [])
        if not isinstance(options, list) or not options:
            return text, None
        options = [str(o) for o in options[:max_options - 1]]
        options.append("Type something different")
        data["options"] = options
        clean = QUIZ_RE.sub("", text).strip()
        return clean, data
    except Exception:
        return text, None


import contextlib


@contextlib.contextmanager
def _keyboard_for_picker():
    """Hand the keyboard to a blocking msvcrt picker by pausing the persistent
    pt input thread for the duration, then restoring it."""
    ctrl = _INPUT_CONTROLLER
    if ctrl is not None:
        ctrl.pause()
    try:
        yield
    finally:
        if ctrl is not None:
            ctrl.resume()


def _pick_option(options: list[str], session) -> str | None:
    """Arrow-key option picker. Returns chosen option text, '__free__' for last option, or None if cancelled."""

    selected = 0
    total = len(options)

    def _render(sel: int):
        for i, opt in enumerate(options):
            if i == sel:
                if i == total - 1:
                    renderer.console.print(f"  [bold {renderer.PERMISSION_BLUE}]❯[/bold {renderer.PERMISSION_BLUE}] [dim]{opt}[/dim]")
                else:
                    renderer.console.print(f"  [bold {renderer.PERMISSION_BLUE}]❯ {opt}[/bold {renderer.PERMISSION_BLUE}]")
            else:
                if i == total - 1:
                    renderer.console.print(f"    [dim]{opt}[/dim]")
                else:
                    renderer.console.print(f"    {opt}")

    _render(selected)

    with _keyboard_for_picker():
        while True:
            ch = _getch()
            if ch == "UP":
                selected = (selected - 1) % total
            elif ch == "DOWN":
                selected = (selected + 1) % total
            elif ch == "ENTER":
                # clear rendered lines
                for _ in range(total):
                    sys.stdout.write("\033[1A\033[2K")
                sys.stdout.flush()
                if selected == total - 1:
                    return "__free__"
                renderer.console.print(f"  [dim]❯ {options[selected]}[/dim]")
                return options[selected]
            elif ch == "ESC" or ch == "\x03":
                for _ in range(total):
                    sys.stdout.write("\033[1A\033[2K")
                sys.stdout.flush()
                return None
            else:
                continue

            # redraw
            for _ in range(total):
                sys.stdout.write("\033[1A\033[2K")
            sys.stdout.flush()
            _render(selected)


def _model_picker(current_model_id: str) -> str | None:
    """Arrow-key model picker grouped by provider. Returns chosen model id, or None if cancelled."""
    from .models import MODELS, PROVIDERS, TIER_COLORS

    # Build a flat list of rows: ("header", provider_name) or ("model", model_dict)
    rows: list[tuple[str, object]] = []
    last_provider = None
    selectable: list[int] = []  # indices into rows that are selectable
    for m in MODELS:
        if m["provider"] != last_provider:
            last_provider = m["provider"]
            p = PROVIDERS.get(last_provider, {})
            rows.append(("header", p.get("name", last_provider)))
        rows.append(("model", m))
        selectable.append(len(rows) - 1)

    # start on current model
    selected_idx = 0
    for si, ri in enumerate(selectable):
        if rows[ri][1]["id"] == current_model_id:
            selected_idx = si
            break

    def _render():
        for ri, (kind, val) in enumerate(rows):
            if kind == "header":
                renderer.console.print(f"  [bold]{val}[/bold]")
                continue
            m = val
            tier_color = TIER_COLORS.get(m["tier"], "white")
            is_cur = m["id"] == current_model_id
            mark = " ◀" if is_cur else ""
            label = f"{m['name']:<24} [{tier_color}]{m['tier']:<10}[/{tier_color}]{mark}"
            if ri == selectable[selected_idx]:
                renderer.console.print(f"  [bold {renderer.PERMISSION_BLUE}]❯ {label}[/bold {renderer.PERMISSION_BLUE}]")
            else:
                renderer.console.print(f"    [dim]{label}[/dim]")

    renderer.console.print()
    renderer.console.print("  [bold]Select a model[/bold]  [dim]↑↓ navigate · Enter select · Esc cancel[/dim]\n")
    _render()
    total_lines = len(rows) + 2

    while True:
        ch = _getch()
        if ch == "UP":
            selected_idx = (selected_idx - 1) % len(selectable)
        elif ch == "DOWN":
            selected_idx = (selected_idx + 1) % len(selectable)
        elif ch == "ENTER":
            for _ in range(total_lines):
                sys.stdout.write("\033[1A\033[2K")
            sys.stdout.flush()
            chosen = rows[selectable[selected_idx]][1]
            renderer.console.print(f"  [dim]❯ {chosen['name']}[/dim]")
            return chosen["id"]
        elif ch == "ESC" or ch == "\x03":
            for _ in range(total_lines):
                sys.stdout.write("\033[1A\033[2K")
            sys.stdout.flush()
            return None
        else:
            continue

        for _ in range(total_lines):
            sys.stdout.write("\033[1A\033[2K")
        sys.stdout.flush()
        renderer.console.print("  [bold]Select a model[/bold]  [dim]↑↓ navigate · Enter select · Esc cancel[/dim]\n")
        _render()


async def _run_quiz_phase(
    session: "PromptSession",
    user_message: str,
    model_id: str,
    mode: str,
    memory_md: str,
    user_md: str,
    deepcode_md: str,
    sys_prompt: str,
    quiz_max_options: int,
    project_memory_md: str = "",
) -> tuple[str, str | None]:
    """
    Run clarification quiz phase before the real response.
    Returns (effective_message, prefetched_final_response | None).
    - If AI never quizzes: returns (user_message, first_response_text) — avoids double API call.
    - If AI quizzes: collects all Q&A, returns (augmented_message, None) — caller runs real response.
    """
    qa_pairs: list[tuple[str, str]] = []

    def _build_prompt(context_block: str) -> str:
        extra = ""
        if deepcode_md:
            extra += f"\n\n[Project context from DEEPCODE.md:\n{deepcode_md}\n]"
        if user_md:
            extra += f"\n\n[User profile:\n{user_md}\n]"
        if memory_md:
            extra += f"\n\n[Memory:\n{memory_md}\n]"
        if project_memory_md:
            extra += f"\n\n[Project memory:\n{project_memory_md}\n]"
        clarify_instruction = (
            "\n\nIf you need more information before acting, ask ONE clarifying question using a <quiz> block. "
            "When you have enough info, respond normally without a <quiz> block."
        )
        return f"{sys_prompt}{extra}{clarify_instruction}\n\nUser: {user_message}{context_block}\nAssistant:"

    context_block = ""
    raw = ""
    try:
        async for chunk in api.stream_chat(_build_prompt(context_block), model_id):
            if chunk.get("delta"):
                raw += chunk["delta"]
            if chunk.get("done"):
                break
    except Exception as e:
        renderer.print_error(str(e))
        return user_message, None

    clean, quiz_data = _parse_quiz(raw, quiz_max_options)

    if not quiz_data:
        # AI didn't want to clarify — return the response as-is, no second call needed
        return user_message, clean

    # AI wants to clarify — loop through questions
    while quiz_data:
        question = quiz_data.get("question", "")
        options = quiz_data["options"]

        if question:
            renderer.console.print(f"\n  [bold cyan]{question}[/bold cyan]")

        # Arrow-key picker — blocking call on main thread (msvcrt requires main thread on Windows)
        result = _pick_option(options, session)
        if result is None:
            break

        if result == "__free__":
            # "Type something different" chosen — read via the persistent bar
            renderer.print_info("Type your answer:")
            free_raw = await _INPUT_CONTROLLER.read_one() if _INPUT_CONTROLLER else None
            if free_raw is None:
                break
            answer = free_raw.strip() or "No preference"
        else:
            answer = result

        qa_pairs.append((question or f"Question {len(qa_pairs)+1}", answer))

        # Ask next question with updated context
        qa_text = "\n".join(f"Q: {q}\nA: {a}" for q, a in qa_pairs)
        context_block = f"\n\n[Clarification so far:\n{qa_text}\n]"

        raw = ""
        try:
            async for chunk in api.stream_chat(_build_prompt(context_block), model_id):
                if chunk.get("delta"):
                    raw += chunk["delta"]
                if chunk.get("done"):
                    break
        except Exception as e:
            renderer.print_error(str(e))
            break

        clean, quiz_data = _parse_quiz(raw, quiz_max_options)

        if not quiz_data:
            # Done clarifying — return augmented message, caller runs real response
            qa_text = "\n".join(f"Q: {q}\nA: {a}" for q, a in qa_pairs)
            return f"{user_message}\n\n[Clarifications:\n{qa_text}\n]", None

    # Interrupted mid-quiz
    if qa_pairs:
        qa_text = "\n".join(f"Q: {q}\nA: {a}" for q, a in qa_pairs)
        return f"{user_message}\n\n[Clarifications:\n{qa_text}\n]", None
    return user_message, None


COMMANDS = [
    ("/notify",         "Toggle bell notification on/off"),
    ("/color",          "Set UI accent color — e.g. /color blue, /color #78aaff"),
    ("/context",        "Scan project for context — /context on|off to toggle auto-scan"),
    ("/reasoning",      "Set reasoning level — off/low/middle/high/ultra"),
    ("/agent",          "Toggle agent mode (file/shell tools)"),
    ("/model",          "Switch model — e.g. /model opus"),
    ("/models",         "List all 34 models"),
    ("/merge",          "Toggle Merge AI mode"),
    ("/search",         "Toggle Web Search mode"),
    ("/session",        "Browse & resume past conversations"),
    ("/new",            "Start new conversation"),
    ("/compact",        "Summarize conversation to save context"),
    ("/history",        "Show past conversations (quick list)"),
    ("/memory",         "Show remembered facts"),
    ("/init",           "Generate DEEPCODE.md for this project"),
    ("/permissions",    "View/manage saved permission rules"),
    ("/quizmaxoptions", "Set max quiz options — e.g. /quizmaxoptions 4"),
    ("/soul",           "View/generate/reset/path — DeepCode personality (SOUL.md)"),
    ("/plan",           "Plan a task step-by-step, then execute or refine"),
    ("/ultracode",      "Spawn hierarchical agent swarm (auto-sized) — e.g. /ultracode build a REST API"),
    ("/workflows",      "View running/recent UltraCode swarms — arrow keys to navigate, enter/esc"),
    ("/keybinds",       "Show all keyboard shortcuts"),
    ("/clear",          "Clear screen"),
    ("/help",           "Show all commands"),
    ("/exit",           "Quit"),
]


_AT_TOKEN_RE = re.compile(r'@([^\s@]*)$')


def _file_suggestions(partial: str, limit: int = 30):
    """Paths under cwd matching `partial` for @-mention completion. Dirs first,
    junk dirs skipped, recurses but bounded. Returns (relpath, is_dir) tuples."""
    import os
    SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea",
            ".vscode", "dist", "build", ".mypy_cache", ".pytest_cache",
            ".deepcodev3", ".egg-info"}
    partial = partial.replace("\\", "/")
    # the directory to list = everything up to the last '/', the rest is a filter
    if "/" in partial:
        base, frag = partial.rsplit("/", 1)
    else:
        base, frag = "", partial
    frag_l = frag.lower()
    try:
        entries = sorted(os.scandir(base or "."),
                         key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return
    n = 0
    for e in entries:
        if e.name in SKIP or any(e.name.endswith(s) for s in (".egg-info",)):
            continue
        if frag_l and not e.name.lower().startswith(frag_l):
            continue
        rel = f"{base}/{e.name}" if base else e.name
        yield (rel, e.is_dir())
        n += 1
        if n >= limit:
            return


_AT_MENTION_RE = re.compile(r'(?<!\S)@([^\s@]+)')
_MAX_MENTION_CHARS = 12000


def expand_at_mentions(text: str) -> tuple[str, list[str]]:
    """Replace @path tokens with the file's content appended as context.
    Returns (model_text, attached_paths). Only paths that resolve to a real
    file under cwd are expanded; missing/dir/oversize ones are left as-is.
    The visible `@path` token stays inline; content is appended below."""
    from pathlib import Path
    attached: list[str] = []
    blocks: list[str] = []
    seen: set[str] = set()
    for m in _AT_MENTION_RE.finditer(text):
        rel = m.group(1).rstrip(".,;:)")  # trailing punctuation isn't part of the path
        if rel in seen:
            continue
        p = Path(rel)
        try:
            if not p.is_file():
                continue
            data = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        seen.add(rel)
        if len(data) > _MAX_MENTION_CHARS:
            data = data[:_MAX_MENTION_CHARS] + "\n... [truncated]"
        blocks.append(f"[Attached file: {rel}]\n{data}")
        attached.append(rel)
    if not blocks:
        return text, []
    return text + "\n\n" + "\n\n".join(blocks), attached


class DeepCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        stripped = text.lstrip()

        # @file mentions — anywhere in the line, not just at the start.
        at = _AT_TOKEN_RE.search(text)
        if at is not None:
            partial = at.group(1)
            for rel, is_dir in _file_suggestions(partial):
                # complete the fragment after the last '/' (or the whole token)
                frag = partial.rsplit("/", 1)[-1]
                insert = rel.rsplit("/", 1)[-1] + ("/" if is_dir else "")
                yield Completion(
                    insert,
                    start_position=-len(frag),
                    display=HTML(f"<b>{rel}{'/' if is_dir else ''}</b>"),
                    display_meta="dir" if is_dir else "file",
                )
            return

        if not stripped.startswith("/"):
            return

        parts = stripped.split(None, 1)
        cmd = parts[0]
        has_arg = len(parts) > 1

        if not has_arg:
            for name, desc in COMMANDS:
                if name.startswith(cmd):
                    yield Completion(
                        name[len(cmd):],
                        start_position=0,
                        display=HTML(f"<cyan>{name}</cyan>"),
                        display_meta=desc,
                    )
        elif cmd == "/model":
            partial = parts[1].lower()
            for m in MODELS:
                if partial in m["name"].lower() or partial in m["provider"].lower():
                    yield Completion(
                        m["name"],
                        start_position=-len(parts[1]),
                        display=HTML(f"<b>{m['name']}</b>"),
                        display_meta=m["provider"],
                    )
        elif cmd == "/color":
            from . import renderer as _r
            partial = parts[1].lower()
            for name, rgb in _r.ACCENT_PRESETS.items():
                if name.startswith(partial):
                    yield Completion(
                        name,
                        start_position=-len(parts[1]),
                        display=HTML(f"<b>{name}</b>"),
                        display_meta=rgb,
                    )


PROMPT_STYLE = Style.from_dict({
    "prompt": "bold cyan",
    "completion-menu.completion":              "bg:#111118 #555566",
    "completion-menu.completion.current":      "bg:#1e1e2e bold #c084fc",
    "completion-menu.meta.completion":         "bg:#111118 #333344",
    "completion-menu.meta.completion.current": "bg:#1e1e2e #888899",
    "scrollbar.background": "bg:#111118",
    "scrollbar.button":     "bg:#c084fc",
})


# Set to the active InputController so the Esc key binding can fire an interrupt
# of the in-flight agent turn from the input thread.
_INPUT_CONTROLLER = None


async def _gen_title(first_message: str, model_id: str) -> str:
    """Ask AI for a short 4-word title for this conversation."""
    prompt = f"Give a 4-word title for a conversation starting with: \"{first_message[:100]}\". Reply with ONLY the title, no quotes, no punctuation."
    title = ""
    try:
        async for chunk in api.stream_chat(prompt, model_id):
            if chunk.get("delta"):
                title += chunk["delta"]
            if chunk.get("done"):
                break
        return title.strip()[:50] or first_message[:40]
    except Exception:
        return first_message[:40]


async def _init_deepcode_md(instructions: str, model_id: str) -> str:
    """Scan cwd and generate a DEEPCODE.md file."""
    from pathlib import Path
    import os

    cwd = Path.cwd()

    # Collect file tree (max depth 3, skip common noise)
    skip = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".egg-info"}
    tree_lines = []
    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in skip]
        depth = len(Path(root).relative_to(cwd).parts)
        if depth > 3:
            dirs.clear()
            continue
        indent = "  " * depth
        rel = Path(root).relative_to(cwd)
        if depth > 0:
            tree_lines.append(f"{indent}{rel.name}/")
        for f in files:
            tree_lines.append(f"{'  ' * (depth+1)}{f}")

    tree = "\n".join(tree_lines[:150])

    # Read key files if they exist
    key_files = ["README.md", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "requirements.txt"]
    snippets = []
    for kf in key_files:
        p = cwd / kf
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8")[:800]
                snippets.append(f"--- {kf} ---\n{content}")
            except Exception:
                pass

    extra = f"\nExtra instructions: {instructions}" if instructions else ""
    prompt = f"""You are generating a DEEPCODE.md file for a software project. This file is like a CLAUDE.md — it gives an AI assistant persistent context about the project so it can help more effectively.

Project file tree:
{tree}

{"Key files:" if snippets else ""}
{chr(10).join(snippets)}
{extra}

Write a DEEPCODE.md that includes:
- What this project is and does
- Tech stack and key dependencies
- Project structure overview
- How to run / build it
- Any important conventions or notes an AI should know

Be concise. Use markdown headers. No fluff."""

    result = ""
    renderer.print_info("Generating DEEPCODE.md...")
    try:
        async for chunk in api.stream_chat(prompt, model_id):
            if chunk.get("delta"):
                result += chunk["delta"]
            if chunk.get("done"):
                break
    except Exception as e:
        renderer.print_error(str(e))
        return ""
    return result.strip()


async def _compact_conversation(conversation: list[dict], model_id: str) -> str:
    """Summarize the conversation so far into a compact context block."""
    history = "\n".join(
        f"{m['role'].upper()}: {m['content'][:500]}"
        for m in conversation[-20:]
    )
    prompt = f"Summarize this conversation concisely so it can be used as context. Keep all important decisions, code, and facts. Be brief.\n\n{history}\n\nSummary:"
    summary = ""
    renderer.print_info("Compacting conversation...")
    try:
        async for chunk in api.stream_chat(prompt, model_id):
            if chunk.get("delta"):
                summary += chunk["delta"]
            if chunk.get("done"):
                break
        return summary.strip()
    except Exception as e:
        renderer.print_error(str(e))
        return ""


def _seed_dir_listing(max_entries: int = 60) -> str:
    """Cheap deterministic listing of the cwd (one level), names + sizes, dirs
    marked. Seeds the bootstrap so the agent doesn't burn a turn on the first
    list_dir. The agent can list_dir deeper (e.g. docs/) on its own."""
    import os
    from pathlib import Path
    SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea",
            ".vscode", "dist", "build", ".mypy_cache", ".pytest_cache"}
    lines = []
    try:
        entries = sorted(os.scandir("."), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return ""
    for e in entries[:max_entries]:
        if e.name in SKIP or e.name.startswith(".") and e.is_dir():
            continue
        if e.is_dir():
            lines.append(f"  {e.name}/   (dir)")
        else:
            try:
                size = e.stat().st_size
            except OSError:
                size = 0
            sz = f"{size}b" if size < 1024 else f"{size//1024}kb"
            lines.append(f"  {e.name}   ({sz})")
    return "\n".join(lines)


async def _bootstrap_context(
    model_id: str,
    memory_md: str,
    user_md: str,
    deepcode_md: str,
    project_memory_md: str,
) -> str:
    """Explore the working directory and return a compact 'project orientation'
    note (a few hundred tokens). Uses the agent's real tool loop so the MODEL
    decides what's worth reading (factoring file name + size), can recurse into
    subdirs (docs/, src/, ...), and reads selectively without blowing context.
    Returns the summary text, or "" on failure / empty dir."""
    from pathlib import Path
    listing = _seed_dir_listing()
    if not listing.strip():
        return ""

    task = (
        "You are starting a fresh session in this working directory. Build a "
        "brief mental model of the project for yourself.\n\n"
        f"Working directory: {Path.cwd()}\n"
        "Top-level contents (name and size):\n"
        f"{listing}\n\n"
        "Do this:\n"
        "1. Decide which files look important from their NAME and SIZE — prefer "
        "README, docs, config/manifest files (package.json, pyproject, Cargo.toml, "
        "go.mod...), and obvious entrypoints. SKIP huge files, lockfiles, binaries, "
        "and generated output.\n"
        "2. Use list_dir to look inside promising subdirectories (e.g. docs/, src/) "
        "and read selectively in there too — go deeper only where it pays off.\n"
        "3. read_file the chosen files. Do NOT read everything; stay cheap. Skip a "
        "file if it's large and unlikely to matter.\n"
        "4. When done, reply with a SHORT orientation note (<=200 words): what this "
        "project is, its layout, the key files and what they do, and how to run it "
        "if obvious. No preamble, no file dumps — just the note."
    )

    conv: list[dict] = []
    try:
        result = await _run_cancelable(
            run_agent(task, conv, memory_md, user_md, model_id,
                      deepcode_md, project_memory_md)
        )
    except Exception as e:
        renderer.print_error(f"Auto-context failed: {e}")
        return ""
    if not result:
        return ""
    summary, _conv = result
    return (summary or "").strip()


def _session_browser(sessions: list[dict]) -> dict | None:
    """Interactive arrow-key session browser. Returns chosen session or None."""
    if not sessions:
        renderer.print_info("No past sessions found.")
        return None

    import msvcrt

    items = list(reversed(sessions[-30:]))  # most recent first
    selected = 0

    def _label(s: dict) -> str:
        title = s.get("title", "Untitled")
        count = len(s.get("messages", []))
        sid = s.get("id", "")[:10]
        return f"{title[:45]:<46} [dim]{count} msgs · {sid}[/dim]"

    def _render(sel: int):
        renderer.console.print("\n  [bold cyan]Sessions[/bold cyan]  [dim]↑↓ navigate · Enter select · Esc cancel[/dim]\n")
        for i, s in enumerate(items):
            if i == sel:
                renderer.console.print(f"  [bold cyan]❯ {_label(s)}[/bold cyan]")
            else:
                renderer.console.print(f"    [dim]{_label(s)}[/dim]")
        renderer.console.print()

    _render(selected)
    total = len(items)

    while True:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            if ch2 == "H":   # up
                selected = (selected - 1) % total
            elif ch2 == "P": # down
                selected = (selected + 1) % total
            else:
                continue
        elif ch == "\r":
            # clear menu
            lines = total + 4
            for _ in range(lines):
                sys.stdout.write("\033[1A\033[2K")
            sys.stdout.flush()
            return items[selected]
        elif ch == "\x1b":
            lines = total + 4
            for _ in range(lines):
                sys.stdout.write("\033[1A\033[2K")
            sys.stdout.flush()
            return None
        else:
            continue

        # redraw
        lines = total + 4
        for _ in range(lines):
            sys.stdout.write("\033[1A\033[2K")
        sys.stdout.flush()
        _render(selected)


async def _run_plan(
    task: str,
    model_id: str,
    memory_md: str,
    user_md: str,
    deepcode_md: str,
    soul_md: str,
    session,
    agent_conversation: list[dict],
    project_memory_md: str = "",
) -> list[dict]:
    """Generate a plan for task, show it, let user execute/refine/cancel. Returns updated agent_conversation."""
    from rich.panel import Panel
    from rich.padding import Padding

    extra = ""
    if soul_md:
        extra += f"[Personality:\n{soul_md}\n]\n\n"
    if deepcode_md:
        extra += f"[Project context:\n{deepcode_md}\n]\n\n"
    if user_md:
        extra += f"[User profile:\n{user_md}\n]\n\n"
    if memory_md:
        extra += f"[Memory:\n{memory_md}\n]\n\n"
    if project_memory_md:
        extra += f"[Project memory:\n{project_memory_md}\n]\n\n"

    # Quiz phase — clarify before planning
    from .system_prompt import SYSTEM_PROMPT as _SP
    effective_task, _ = await _run_quiz_phase(
        session, task, model_id, "chat", memory_md, user_md, deepcode_md, _SP, DEFAULT_QUIZ_MAX,
        project_memory_md=project_memory_md,
    )

    plan_prompt = (
        f"{extra}You are a planning assistant. The user wants to accomplish the following task:\n\n"
        f"{effective_task}\n\n"
        "Generate a clear, numbered step-by-step plan. For each step include:\n"
        "- What to do\n"
        "- Why (one sentence)\n"
        "- Any risk or caveat (if relevant)\n\n"
        "Be concrete and actionable. No fluff. Output ONLY the plan, no intro text. "
        "CRITICAL: Do NOT ask questions. Do NOT request clarification. Make reasonable assumptions and plan anyway."
    )

    renderer.print_info("Planning...")
    plan_text = ""
    try:
        async for chunk in api.stream_chat(plan_prompt, model_id):
            if chunk.get("delta"):
                plan_text += chunk["delta"]
            if chunk.get("done"):
                break
    except Exception as e:
        renderer.print_error(str(e))
        return agent_conversation

    plan_text = plan_text.strip()

    # Display plan in a panel
    renderer.console.print()
    renderer.console.print(Padding(
        Panel(
            plan_text,
            title="[bold cyan]Plan[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        ),
        pad=(0, 0, 0, 2),
    ))

    # Ask what to do
    options = ["Execute this plan", "Refine the plan", "Cancel"]
    renderer.console.print(f"\n  [bold cyan]What do you want to do?[/bold cyan]")
    renderer.print_quiz(options)
    choice = _pick_option(options, session)

    if choice is None or choice == "Cancel":
        renderer.print_info("Plan cancelled.")
        return agent_conversation

    if choice == "__free__" or choice == "Refine the plan":
        renderer.print_info("What should be changed?")
        feedback = await _INPUT_CONTROLLER.read_one() if _INPUT_CONTROLLER else None
        if feedback is None:
            return agent_conversation
        feedback = feedback.strip()

        refine_prompt = (
            f"{extra}Here is a plan for: {task}\n\n{plan_text}\n\n"
            f"User feedback: {feedback}\n\n"
            "Rewrite the plan incorporating this feedback. Output ONLY the updated plan."
        )
        renderer.print_info("Refining...")
        refined = ""
        try:
            async for chunk in api.stream_chat(refine_prompt, model_id):
                if chunk.get("delta"):
                    refined += chunk["delta"]
                if chunk.get("done"):
                    break
        except Exception as e:
            renderer.print_error(str(e))
            return agent_conversation

        plan_text = refined.strip()
        renderer.console.print()
        renderer.console.print(Padding(
            Panel(
                plan_text,
                title="[bold cyan]Refined Plan[/bold cyan]",
                border_style="cyan",
                padding=(0, 1),
            ),
            pad=(0, 0, 0, 2),
        ))

        # Ask again after refinement
        options2 = ["Execute this plan", "Cancel"]
        renderer.console.print(f"\n  [bold cyan]Execute refined plan?[/bold cyan]")
        renderer.print_quiz(options2)
        choice2 = _pick_option(options2, session)
        if choice2 is None or choice2 == "Cancel" or choice2 == "__free__":
            renderer.print_info("Plan cancelled.")
            return agent_conversation

    # Execute — pass plan + task to agent
    execute_msg = (
        f"Execute the following plan for this task: {task}\n\n"
        f"Plan:\n{plan_text}\n\n"
        "Execute every step using tools. Use write_file to create files, run_command to run commands. "
        "If you need clarification use a <quiz> block — otherwise proceed with best judgment. "
        "Only speak after tool results confirm work. Complete all steps."
    )
    renderer.print_info("Executing plan...")
    cancel_result = await _run_cancelable(run_agent(execute_msg, agent_conversation, memory_md, user_md, model_id, deepcode_md, project_memory_md))
    if cancel_result is None:
        return agent_conversation
    raw_agent, agent_conversation = cancel_result
    # Handle quiz responses from agent during execution
    content, agent_quiz = _parse_quiz(raw_agent, DEFAULT_QUIZ_MAX)
    qa_pairs: list[tuple[str, str]] = []
    while agent_quiz:
        question = agent_quiz.get("question", "")
        if question:
            renderer.console.print(f"\n  [bold cyan]{question}[/bold cyan]")
        result = _pick_option(agent_quiz["options"], session)
        if result is None:
            break
        if result == "__free__":
            renderer.print_info("Type your answer:")
            free = await _INPUT_CONTROLLER.read_one() if _INPUT_CONTROLLER else None
            if free is None:
                break
            answer = free.strip() or "No preference"
        else:
            answer = result
        qa_pairs.append((question or f"Question {len(qa_pairs)+1}", answer))
        qa_text = "\n".join(f"Q: {q}\nA: {a}" for q, a in qa_pairs)
        followup = (
            f"[Clarification answers so far:\n{qa_text}\n]\n\n"
            "Proceed with the plan using these answers. Do not re-ask the same questions. Act."
        )
        cancel_result = await _run_cancelable(run_agent(followup, agent_conversation, memory_md, user_md, model_id, deepcode_md, project_memory_md))
        if cancel_result is None:
            break
        raw_agent, agent_conversation = cancel_result
        _, agent_quiz = _parse_quiz(raw_agent, DEFAULT_QUIZ_MAX)
    return agent_conversation


async def _run_ultracode(
    arg: str,
    model_id: str,
    memory_md: str,
    user_md: str,
    deepcode_md: str,
    project_memory_md: str,
    agent_conversation: list[dict],
    current_session: dict,
) -> None:
    """
    Parse /ultracode <task> and launch the swarm in the background. The leader
    LLM decides how many agents the task needs — no manual sizing required.
    The REPL stays usable while the swarm runs; use /workflows to watch progress.
    On completion, results are injected into agent_conversation and current_session.

    Example:
      /ultracode build a full REST API with auth
    """
    from .ultracode import UltraCodeOrchestrator, SwarmState, ACTIVE_SWARMS
    import uuid

    task = arg.strip()

    if not task:
        renderer.print_error("Usage: /ultracode <task description>")
        return

    swarm_state = SwarmState(id=str(uuid.uuid4())[:8], task=task)
    ACTIVE_SWARMS.append(swarm_state)

    renderer.console.print()
    renderer.console.print(
        f"  [bold magenta]UltraCode[/bold magenta]  "
        f"[dim]{task[:80]} — running in background, use /workflows to watch[/dim]"
    )
    renderer.console.print()

    async def _runner():
        t0 = time.time()
        orchestrator = UltraCodeOrchestrator(
            task=task,
            model_id=model_id,
            memory_md=memory_md,
            user_md=user_md,
            deepcode_md=deepcode_md,
            project_memory_md=project_memory_md,
            swarm_state=swarm_state,
        )

        try:
            results = await orchestrator.run()
            summary = orchestrator.synthesize(results)
            elapsed = time.time() - t0

            renderer.console.print()
            renderer.console.print(f"  [bold magenta]UltraCode[/bold magenta]  [dim]finished: {task[:60]} ({elapsed:.0f}s)[/dim]")
            renderer.print_assistant_header(model_id)
            renderer.finish_stream(summary)
            renderer.print_response_time(elapsed)

            # Build a compact context block summarising what the swarm produced,
            # then inject it into agent_conversation and current_session so that
            # any follow-up message sees exactly what was built and where.
            done_groups = [r for r in results if r.status.value == "done"]
            files_all = [f for r in done_groups for f in r.artifact.files]

            context_lines = [
                f"[UltraCode swarm results — task: {task}]",
                f"Groups completed: {len(done_groups)}/{len(results)}",
            ]
            if files_all:
                context_lines.append(f"Files written: {', '.join(files_all)}")
            for r in done_groups:
                snippet = r.artifact.content[:400].replace("\n", " ")
                context_lines.append(f"  {r.group_id} ({r.artifact.name}): {snippet}")

            swarm_context = "\n".join(context_lines)

            # Inject as a user+assistant exchange so it appears in conversation history
            agent_conversation.append({"role": "user", "content": f"[UltraCode task]: {task}"})
            agent_conversation.append({"role": "assistant", "content": swarm_context})
            current_session["messages"].append({"role": "user", "content": f"[UltraCode task]: {task}", "model": model_id})
            current_session["messages"].append({"role": "assistant", "content": swarm_context, "model": model_id})

            swarm_state.finished = True

        except Exception as e:
            swarm_state.finished = True
            swarm_state.failed = True
            swarm_state.error = str(e)
            renderer.print_error(f"UltraCode failed: {e}")

    asyncio.get_event_loop().create_task(_runner())


async def run_chat_stream(message: str, model_id: str, mode: str, memory_md: str = "", deepcode_md: str = "", sys_prompt: str = ""):
    full_content = ""
    reasoning = ""
    t0 = time.time()
    if not sys_prompt:
        sys_prompt = SYSTEM_PROMPT

    try:
        if mode == "merge":
            async for chunk in api.stream_merge(message):
                if chunk.get("status"):
                    renderer.print_status(chunk["status"])
                if chunk.get("delta"):
                    full_content += chunk["delta"]
                    renderer.stream_token(chunk["delta"])
                if chunk.get("reasoning"):
                    reasoning = chunk["reasoning"]
                if chunk.get("answer"):
                    full_content = chunk["answer"]
                if chunk.get("done"):
                    break

        elif mode == "search":
            async for chunk in api.stream_search(message):
                if chunk.get("status"):
                    renderer.print_status(chunk["status"])
                if chunk.get("delta"):
                    full_content += chunk["delta"]
                    renderer.stream_token(chunk["delta"])
                if chunk.get("sources"):
                    renderer.print_search_sources(chunk["sources"])
                if chunk.get("done"):
                    break

        else:
            extra = ""
            if deepcode_md:
                extra += f"\n\n[Project context from DEEPCODE.md:\n{deepcode_md}\n]"
            if memory_md:
                extra += f"\n\n[Memory:\n{memory_md}\n]"
            prompt = f"{sys_prompt}{extra}\n\nUser: {message}\nAssistant:"

            async for chunk in api.stream_chat(prompt, model_id):
                if chunk.get("delta"):
                    full_content += chunk["delta"]
                    renderer.stream_token(chunk["delta"])
                if chunk.get("reasoning"):
                    reasoning = chunk["reasoning"]
                if chunk.get("answer"):
                    full_content = chunk["answer"]
                if chunk.get("error"):
                    renderer.print_error(chunk["error"])
                if chunk.get("done"):
                    break

    except asyncio.CancelledError:
        if full_content.strip():
            renderer.print_assistant_header(model_id, mode)
            renderer.finish_stream(full_content)
        renderer.print_info("Stopped.")
        return full_content, reasoning
    except Exception as e:
        renderer.print_error(str(e))
        return "", ""

    elapsed = time.time() - t0

    if full_content.strip():
        renderer.print_assistant_header(model_id, mode)
        display = QUIZ_RE.sub("", full_content).strip()
        renderer.finish_stream(display)
        if reasoning:
            renderer.print_reasoning(reasoning)
        renderer.print_response_time(elapsed)

    return full_content, reasoning


async def main_loop(resume=None):
    """resume: None = fresh session; "last" = continue most recent; a session id
    string = resume that specific conversation; "pick" = browse and choose."""
    cfg = storage.load_config()
    model_id = cfg.get("model", DEFAULT_MODEL)
    mode = cfg.get("mode", "chat")
    agent_mode = cfg.get("agent", False)
    reasoning_level = cfg.get("reasoning", None)  # None = off
    quiz_max_options = cfg.get("quiz_max_options", DEFAULT_QUIZ_MAX)
    renderer.set_notify(cfg.get("notify", True))

    storage.ensure_dir()
    history_path = storage.DATA_DIR / "prompt_history"

    def _mode_line():
        # Claude-Code-style mode line shown beneath the input box.
        from .models import get_model
        m = get_model(model_id)
        # Lead with the permission mode (the shift+tab line) when tools are on.
        if agent_mode:
            lead = permissions.mode_label()
        else:
            lead = "chat mode"
        parts = [lead, m["name"]]
        if mode != "chat":  parts.append(mode)
        if reasoning_level: parts.append(f"reasoning:{reasoning_level}")
        line = "  ·  ".join(parts)
        ctrl = _INPUT_CONTROLLER
        if ctrl is not None:
            if ctrl.pending:
                line += f"  ·  {len(ctrl.pending)} queued"
            if ctrl._busy:
                # live token count + elapsed + interrupt hint
                line += ctrl.status_suffix() or "  ·  running (Esc to interrupt)"
        return line

    permissions.set_mode(cfg.get("perm_mode", permissions.MODE_DEFAULT))

    # accent color (/color) — apply before banner so the logo renders in it
    accent_rgb = renderer.resolve_color(cfg.get("accent", "orange")) or renderer.ACCENT
    renderer.set_accent(accent_rgb)

    def _cycle_perm_mode():
        new_mode = permissions.cycle_mode()
        cfg["perm_mode"] = new_mode
        storage.save_config(cfg)

    def _turn_event(ev: dict):
        # accumulate live token count for the status line
        if ev.get("type") == "tokens":
            ctrl = _INPUT_CONTROLLER
            if ctrl is not None:
                ctrl.update_turn(ctrl._turn_tokens + int(ev.get("count", 0)))

    global _INPUT_CONTROLLER
    controller = InputController(
        history=FileHistory(str(history_path)),
        completer=DeepCompleter(),
        style=PROMPT_STYLE,
        mode_line_fn=_mode_line,
        mode_cycle_cb=_cycle_perm_mode,
        prompt_symbol="❯ ",
        accent=renderer.accent_hex(),
    )
    _INPUT_CONTROLLER = controller
    controller.start(asyncio.get_event_loop())

    renderer.print_banner(model_id)
    renderer.print_model_status(model_id, mode, agent_mode)

    sessions = storage.load_history()
    memory_md = storage.load_memory_md()
    user_md = storage.load_user_md()
    project_memory_md = storage.load_project_memory_md()
    deepcode_md = storage.load_deepcode_md()
    soul_md = storage.load_soul_md()
    if deepcode_md:
        renderer.print_info("DEEPCODE.md loaded.")
    if soul_md:
        renderer.print_info("SOUL.md loaded.")
    # Resume a prior conversation (-c / --resume <id>) or start fresh.
    current_session = None
    agent_conversation: list[dict] = []
    if resume is not None:
        target = None
        if resume == "last":
            target = sessions[-1] if sessions else None
        elif resume == "pick":
            target = _session_browser(sessions)
        else:
            target = next((s for s in sessions if s.get("id") == resume), None)
            if target is None:
                # allow a short id prefix
                target = next((s for s in sessions if s.get("id", "").startswith(resume)), None)
        if target is not None:
            current_session = target
            agent_conversation = [
                {"role": m["role"], "content": m["content"]}
                for m in target.get("messages", [])
                if m["role"] in ("user", "assistant")
            ]
            model_id = target.get("model", model_id)
            renderer.print_info(f"Resumed: {target.get('title', target.get('id', 'session'))}")
        else:
            renderer.print_error(
                f"No session to resume ({resume!r}). Starting fresh." if resume != "last"
                else "No previous conversation. Starting fresh.")
    if current_session is None:
        current_session = storage.new_session(model_id)
    stream_task = None

    auto_context = cfg.get("auto_context", True)

    async def _run_bootstrap():
        """Explore the project and inject a compact orientation note into the
        conversation. Needs tools, so it temporarily uses the agent loop."""
        from rich.panel import Panel
        from rich.padding import Padding
        from rich.text import Text
        from rich import box
        renderer.print_info("Scanning project for context...")
        summary = await _bootstrap_context(
            model_id, memory_md, user_md, deepcode_md, project_memory_md)
        if not summary:
            renderer.print_info("No project context gathered.")
            return
        note = f"[Project context]\n{summary}"
        agent_conversation.append({"role": "assistant", "content": note})
        current_session["messages"].append(
            {"role": "assistant", "content": note, "model": model_id})
        renderer.console.print()
        renderer.console.print(Padding(
            Panel(Text(summary, style="white"),
                  title=f"[{renderer.CLAUDE_ORANGE} bold]Project context[/{renderer.CLAUDE_ORANGE} bold]",
                  border_style=renderer.SUBTLE_GRAY, box=box.ROUNDED, padding=(0, 1)),
            pad=(0, 0, 0, 1)))
        renderer.console.print()

    if auto_context:
        await _run_bootstrap()

    try:
      while True:
        raw = await controller.get_line()
        if raw is None:
            # EOF / Ctrl-D / shutdown
            renderer.print_info("\nBye!")
            break

        text = raw.strip()
        if not text:
            continue

        # pending is maintained inside the controller (the input thread appends,
        # get_line popped this one off the front). Echo the submitted line into
        # the scrollback so it stays visible above the input bar.
        renderer.print_user_line(text)

        if text.startswith("/"):
            parts = text.split(None, 1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd == "/exit":
                renderer.print_info("Bye!")
                break

            elif cmd == "/clear":
                renderer.console.clear()
                renderer.print_banner(model_id)
                renderer.print_model_status(model_id, mode, agent_mode)

            elif cmd == "/new":
                if current_session["messages"]:
                    sessions.append(current_session)
                    storage.save_history(sessions)
                current_session = storage.new_session(model_id)
                agent_conversation = []
                renderer.print_info("New conversation started.")

            elif cmd == "/session":
                chosen = _session_browser(sessions)
                if chosen:
                    current_session = chosen
                    agent_conversation = [
                        {"role": m["role"], "content": m["content"]}
                        for m in chosen.get("messages", [])
                        if m["role"] in ("user", "assistant")
                    ]
                    title = chosen.get("title", "Untitled")
                    renderer.print_info(f"Resumed: {title}")

            elif cmd == "/compact":
                if not current_session["messages"]:
                    renderer.print_info("No conversation to compact.")
                else:
                    summary = await _compact_conversation(current_session["messages"], model_id)
                    if summary:
                        current_session["messages"] = [{
                            "role": "user",
                            "content": f"[Conversation summary]\n{summary}"
                        }]
                        agent_conversation = [{"role": "user", "content": f"[Conversation summary]\n{summary}"}]
                        renderer.print_info("Conversation compacted.")
                        if auto_context:
                            await _run_bootstrap()

            elif cmd == "/model":
                if arg:
                    found = find_model(arg)
                    if found:
                        model_id = found["id"]
                        mode = "chat"
                        cfg["model"] = model_id
                        cfg["mode"] = mode
                        storage.save_config(cfg)
                        renderer.print_model_status(model_id, mode, agent_mode)
                    else:
                        renderer.print_error(f"No model matching '{arg}'. Try /models.")
                else:
                    chosen_id = _model_picker(model_id)
                    if chosen_id:
                        model_id = chosen_id
                        mode = "chat"
                        cfg["model"] = model_id
                        cfg["mode"] = mode
                        storage.save_config(cfg)
                        renderer.print_model_status(model_id, mode, agent_mode)

            elif cmd == "/models":
                renderer.print_models_list(model_id)

            elif cmd == "/merge":
                mode = "chat" if mode == "merge" else "merge"
                cfg["mode"] = mode
                storage.save_config(cfg)
                renderer.print_model_status(model_id, mode, agent_mode)

            elif cmd == "/search":
                mode = "chat" if mode == "search" else "search"
                cfg["mode"] = mode
                storage.save_config(cfg)
                renderer.print_model_status(model_id, mode, agent_mode)

            elif cmd == "/agent":
                agent_mode = not agent_mode
                cfg["agent"] = agent_mode
                storage.save_config(cfg)
                agent_conversation = []
                renderer.print_model_status(model_id, mode, agent_mode)

            elif cmd == "/init":
                content = await _init_deepcode_md(arg, model_id)
                if content:
                    from pathlib import Path
                    out = Path.cwd() / "DEEPCODE.md"
                    out.write_text(content, encoding="utf-8")
                    deepcode_md = content
                    renderer.print_info(f"DEEPCODE.md written to {out}")

            elif cmd == "/memory":
                renderer.print_memory(memory_md, user_md, project_memory_md)

            elif cmd == "/permissions":
                sub = arg.strip()
                if sub.lower().startswith("remove "):
                    idx_str = sub.split(None, 1)[1].strip()
                    if idx_str.isdigit():
                        if permissions.remove_rule(int(idx_str)):
                            renderer.print_info(f"Removed rule #{idx_str}.")
                        else:
                            renderer.print_error(f"No rule #{idx_str}.")
                    else:
                        renderer.print_error("Usage: /permissions remove <n>")
                else:
                    renderer.print_permission_rules(permissions.list_rules())

            elif cmd == "/history":
                all_s = sessions + ([current_session] if current_session["messages"] else [])
                if not all_s:
                    renderer.print_info("No history yet.")
                else:
                    renderer.console.print()
                    for s in all_s[-10:]:
                        title = s.get("title", "Untitled")
                        count = len(s.get("messages", []))
                        renderer.console.print(f"  [cyan]{title[:50]}[/cyan]  [dim]{count} messages · {s['id']}[/dim]")
                    renderer.console.print()

            elif cmd == "/reasoning":
                lvl = arg.lower().strip()
                if lvl == "off" or (not lvl and reasoning_level):
                    reasoning_level = None
                    cfg["reasoning"] = None
                    renderer.print_info("Reasoning OFF.")
                elif lvl in REASONING_LEVELS:
                    reasoning_level = lvl
                    cfg["reasoning"] = lvl
                    renderer.print_info(f"Reasoning: {lvl}")
                else:
                    renderer.print_error(f"Unknown level '{lvl}'. Use: off, low, middle, high, ultra")
                storage.save_config(cfg)

            elif cmd == "/color":
                spec = arg.strip()
                if not spec:
                    names = ", ".join(renderer.ACCENT_PRESETS.keys())
                    cur = cfg.get("accent", "orange")
                    renderer.print_info(f"Current color: {cur}. Usage: /color <name|#hex|rgb(r,g,b)>")
                    renderer.print_info(f"Presets: {names}")
                else:
                    resolved = renderer.resolve_color(spec)
                    if resolved is None:
                        renderer.print_error(f"Bad color '{spec}'. Try a preset name, #rrggbb, or rgb(r,g,b).")
                    else:
                        renderer.set_accent(resolved)
                        controller.set_accent(renderer.accent_hex())
                        cfg["accent"] = spec.lower()
                        storage.save_config(cfg)
                        renderer.console.clear()
                        renderer.print_banner(model_id)
                        renderer.print_model_status(model_id, mode, agent_mode)
                        renderer.print_info(f"Color set to {spec}.")

            elif cmd == "/context":
                sub = arg.strip().lower()
                if sub == "off":
                    auto_context = False
                    cfg["auto_context"] = False
                    storage.save_config(cfg)
                    renderer.print_info("Auto-context OFF (won't scan on start/compact).")
                elif sub == "on":
                    auto_context = True
                    cfg["auto_context"] = True
                    storage.save_config(cfg)
                    renderer.print_info("Auto-context ON.")
                else:
                    await _run_bootstrap()

            elif cmd == "/notify":
                new_state = not renderer._notify
                renderer.set_notify(new_state)
                cfg["notify"] = new_state
                storage.save_config(cfg)
                renderer.print_info(f"Bell notifications {'ON' if new_state else 'OFF'}.")

            elif cmd == "/quizmaxoptions":
                if arg.strip().isdigit():
                    n = int(arg.strip())
                    if 2 <= n <= 10:
                        quiz_max_options = n
                        cfg["quiz_max_options"] = n
                        storage.save_config(cfg)
                        renderer.print_info(f"Quiz max options: {n} (last is always 'Type something different')")
                    else:
                        renderer.print_error("Must be between 2 and 10.")
                else:
                    renderer.print_error(f"Usage: /quizmaxoptions <number>  (current: {quiz_max_options})")

            elif cmd == "/soul":
                sub = arg.strip().lower()
                if sub == "path":
                    renderer.print_info(str(storage.SOUL_FILE))
                elif sub == "reset":
                    storage.delete_soul_md()
                    soul_md = ""
                    renderer.print_info("SOUL.md deleted. Using default personality.")
                elif sub == "show" or (not sub and soul_md):
                    if soul_md:
                        renderer.console.print()
                        renderer.console.print(f"  [bold]SOUL.md[/bold]  [dim]{storage.SOUL_FILE}[/dim]")
                        renderer.console.print(f"  [dim]{len(soul_md)}/1024 chars[/dim]")
                        renderer.console.print()
                        for line in soul_md.splitlines():
                            renderer.console.print(f"  {line}", markup=False)
                        renderer.console.print()
                    else:
                        renderer.print_info("No SOUL.md yet. Use /soul generate to create one.")
                elif sub == "generate" or (not sub and not soul_md):
                    renderer.print_info("Generating SOUL.md via AI...")
                    soul_prompt = (
                        "Generate a SOUL.md file for an AI terminal assistant called DeepCode. "
                        "This file defines its personality, tone, and values. "
                        "Be concise — strict 1024 character limit. "
                        "Write in second person (\"You are...\", \"You value...\"). "
                        "Make it direct, technical, slightly witty, no corporate speak. "
                        "Output ONLY the raw personality text, no markdown headers, no preamble."
                    )
                    generated = ""
                    try:
                        async for chunk in api.stream_chat(soul_prompt, model_id):
                            if chunk.get("delta"):
                                generated += chunk["delta"]
                            if chunk.get("done"):
                                break
                        generated = generated.strip()[:1024]
                        storage.save_soul_md(generated)
                        soul_md = generated
                        renderer.print_info(f"SOUL.md generated ({len(generated)} chars) → {storage.SOUL_FILE}")
                    except Exception as e:
                        renderer.print_error(str(e))
                else:
                    renderer.print_info("Usage: /soul [show|generate|reset|path]")

            elif cmd == "/plan":
                if not arg.strip():
                    renderer.print_error("Usage: /plan <task description>")
                else:
                    agent_conversation = await _run_plan(
                        arg.strip(), model_id, memory_md, user_md, deepcode_md, soul_md, None, agent_conversation, project_memory_md
                    )

            elif cmd == "/ultracode":
                await _run_ultracode(arg.strip(), model_id, memory_md, user_md, deepcode_md, project_memory_md, agent_conversation, current_session)

            elif cmd == "/workflows":
                from .workflows_ui import run_workflows_ui
                await run_workflows_ui()

            elif cmd == "/keybinds":
                renderer.print_keybinds()

            elif cmd in ("/help", "/?"):
                renderer.print_help(agent_mode, COMMANDS)

            else:
                renderer.print_error(f"Unknown command '{cmd}'. Type /help.")

            continue

        # Intent detection (plan / ultracode) must run on the USER's words, not
        # on any @-mentioned file content — capture before expansion.
        _intent = text.lower()

        # @file mentions — inline the referenced files' content as context.
        # The raw @path was already echoed; expand only the model-bound text.
        if "@" in text:
            text, _attached = expand_at_mentions(text)
            if _attached:
                renderer.print_info("Attached: " + ", ".join(_attached))

        # Detect planning intent in natural language
        _tl = _intent
        _PLAN_TRIGGERS = ("make a plan", "plan out", "plan this", "create a plan", "write a plan", "give me a plan", "let's plan", "lets plan")
        if any(t in _tl for t in _PLAN_TRIGGERS):
            agent_conversation = await _run_plan(
                text, model_id, memory_md, user_md, deepcode_md, soul_md, None, agent_conversation, project_memory_md
            )
            continue

        # Detect ultracode swarm intent in natural language — "ultracode" anywhere
        # in the message triggers the swarm with the rest of the message as the task.
        if "ultracode" in _tl:
            import re as _re
            swarm_task = _re.sub(r'(?i)\bultracode\b', '', text).strip(" ,.:;-")
            if not swarm_task:
                swarm_task = text
            await _run_ultracode(swarm_task, model_id, memory_md, user_md, deepcode_md, project_memory_md, agent_conversation, current_session)
            continue

        # send message
        sys_prompt = SYSTEM_PROMPT.replace("{max_options}", str(quiz_max_options - 1))
        if soul_md:
            sys_prompt = f"[Personality:\n{soul_md}\n]\n\n{sys_prompt}"

        # Quiz clarification phase — AI may ask questions before acting.
        # Returns (effective_message, prefetched_response_or_None).
        # If prefetched is not None, AI skipped quizzing and already answered — show it directly.
        effective_text, prefetched = await _run_quiz_phase(
            None, text, model_id, mode, memory_md, user_md, deepcode_md, sys_prompt, quiz_max_options,
            project_memory_md=project_memory_md,
        )

        current_session["messages"].append({"role": "user", "content": effective_text, "model": model_id})

        # auto-generate title from first user message
        if len(current_session["messages"]) == 1 and not current_session.get("title"):
            asyncio.get_event_loop().create_task(
                _set_title(current_session, effective_text, model_id)
            )

        if prefetched is not None and not agent_mode and not reasoning_level and mode == "chat":
            # AI answered directly in the quiz probe — just display it
            renderer.print_assistant_header(model_id, mode)
            renderer.finish_stream(prefetched)
            content = prefetched
        elif agent_mode and mode == "chat":
            cancel_result = await _run_cancelable(run_agent(effective_text, agent_conversation, memory_md, user_md, model_id, deepcode_md, project_memory_md, on_event=_turn_event))
            if cancel_result is None:
                continue
            raw_agent, agent_conversation = cancel_result
            content, agent_quiz = _parse_quiz(raw_agent, quiz_max_options)
            # Loop: agent may ask multiple clarifying questions in sequence.
            qa_pairs: list[tuple[str, str]] = []
            while agent_quiz:
                question = agent_quiz.get("question", "")
                if question:
                    renderer.console.print(f"\n  [bold cyan]{question}[/bold cyan]")
                result = _pick_option(agent_quiz["options"], None)
                if result is None:
                    break
                if result == "__free__":
                    renderer.print_info("Type your answer:")
                    free = await _INPUT_CONTROLLER.read_one() if _INPUT_CONTROLLER else None
                    if free is None:
                        break
                    answer = free.strip() or "No preference"
                else:
                    answer = result
                qa_pairs.append((question or f"Question {len(qa_pairs)+1}", answer))
                # Carry ALL Q&A forward so the agent has full clarification context.
                qa_text = "\n".join(f"Q: {q}\nA: {a}" for q, a in qa_pairs)
                followup = (
                    f"[Clarification answers so far:\n{qa_text}\n]\n\n"
                    "Proceed with the original task using these answers. "
                    "Do not re-ask the same questions. If you still need more info, ask a NEW question via <quiz>; otherwise act."
                )
                cancel_result = await _run_cancelable(run_agent(followup, agent_conversation, memory_md, user_md, model_id, deepcode_md, project_memory_md))
                if cancel_result is None:
                    break
                raw_agent, agent_conversation = cancel_result
                content_next, agent_quiz = _parse_quiz(raw_agent, quiz_max_options)
                if content_next:
                    content = content_next
        elif reasoning_level and mode == "chat":
            memory_block = ""
            if deepcode_md:
                memory_block += f"[Project context from DEEPCODE.md:\n{deepcode_md}\n]"
            if user_md:
                memory_block += f"\n\n[User profile:\n{user_md}\n]"
            if memory_md:
                memory_block += f"\n\n[Memory:\n{memory_md}\n]"
            if project_memory_md:
                memory_block += f"\n\n[Project memory:\n{project_memory_md}\n]"
            renderer.print_assistant_header(model_id)
            raw_content = await _run_cancelable(
                run_reasoning(effective_text, model_id, reasoning_level, sys_prompt, memory_block.strip())
            )
            if raw_content is None:
                continue
            content, _ = _parse_quiz(raw_content, quiz_max_options)
            renderer.finish_stream(content)
        else:
            cr = await _run_cancelable(
                run_chat_stream(effective_text, model_id, mode, memory_md, deepcode_md, sys_prompt)
            )
            if cr is None:
                continue
            raw_content, reasoning = cr
            content, _ = _parse_quiz(raw_content, quiz_max_options)

        if content:
            current_session["messages"].append({
                "role": "assistant",
                "content": content,
                "model": model_id if mode == "chat" else f"__{mode}__",
            })
            _persist_session(sessions, current_session)

            # Auto-extract facts — split into global/project/user buckets
            if len(current_session["messages"]) >= 2:
                extracted = await api.extract_memory_split(
                    current_session["messages"][-2:], memory_md, project_memory_md, user_md
                )

                def _merge_facts(existing: str, new_facts: list[str], max_chars: int) -> str:
                    lines = [l for l in existing.splitlines() if l.strip()]
                    for f in new_facts:
                        line = f"- {f}" if not f.startswith("-") else f
                        if line not in lines:
                            lines.append(line)
                    return "\n".join(lines)

                if extracted.get("global"):
                    memory_md = _merge_facts(memory_md, extracted["global"], storage.MEMORY_MD_MAX_CHARS)
                    if storage.needs_compression(memory_md, storage.MEMORY_MD_MAX_CHARS):
                        memory_md = await api.compress_memory(memory_md, "global personal")
                    storage.save_memory_md(memory_md)

                if extracted.get("project"):
                    project_memory_md = _merge_facts(project_memory_md, extracted["project"], storage.PROJECT_MEMORY_MAX_CHARS)
                    if storage.needs_compression(project_memory_md, storage.PROJECT_MEMORY_MAX_CHARS):
                        project_memory_md = await api.compress_memory(project_memory_md, "project-specific")
                    storage.save_project_memory_md(project_memory_md)

                if extracted.get("user"):
                    user_md = _merge_facts(user_md, extracted["user"], storage.USER_MD_MAX_CHARS)
                    if storage.needs_compression(user_md, storage.USER_MD_MAX_CHARS):
                        user_md = await api.compress_memory(user_md, "user identity")
                    storage.save_user_md(user_md)
    finally:
        controller.stop()
        _INPUT_CONTROLLER = None
        # Print how to resume THIS conversation next time.
        if current_session.get("messages"):
            sid = current_session.get("id", "")
            renderer.print_info(
                f"Resume this chat:  deepcode --resume {sid}   (or  deepcode -c  for the latest)")


async def _set_title(session_obj: dict, first_msg: str, model_id: str):
    """Background task — generate and save title."""
    title = await _gen_title(first_msg, model_id)
    session_obj["title"] = title


def _persist_session(sessions: list[dict], current: dict):
    """Save history with `current` merged in by id (no duplicate when resuming
    an existing session)."""
    merged = [s for s in sessions if s.get("id") != current.get("id")]
    merged.append(current)
    storage.save_history(merged[-100:])


def _parse_cli_args(argv: list[str]):
    """-c/--continue -> resume latest; --resume [id] -> resume id (or pick if
    no id). Returns the `resume` value for main_loop, or None."""
    args = argv[1:]
    for i, a in enumerate(args):
        if a in ("-c", "--continue"):
            return "last"
        if a in ("-r", "--resume"):
            nxt = args[i + 1] if i + 1 < len(args) else None
            return nxt if nxt and not nxt.startswith("-") else "pick"
    return None


def run():
    asyncio.run(main_loop(resume=_parse_cli_args(sys.argv)))
