from __future__ import annotations
import fnmatch
import sys
from pathlib import Path
from urllib.parse import urlparse

from . import storage
from .tool_schema import category_for_tool

OPTION_ALLOW       = "allow"
OPTION_ALLOW_ALWAYS = "allow_always"
OPTION_DENY        = "deny"
OPTION_DENY_ALWAYS = "deny_always"

# ── permission modes (toggled with shift+tab) ─────────────────────────────────
# default  : prompt before every non-read tool call (the safe baseline)
# auto     : auto-allow ALL tool calls (hard-deny patterns still apply)
# readonly : allow read-category tools, silently DENY all write/exec/network.
#            Used by the GUI when "Agent" is off — no interactive picker needed
#            (the GUI can't show one yet), and no shell access to strangers.
MODE_DEFAULT = "default"
MODE_AUTO = "auto"
MODE_READONLY = "readonly"
_MODE_CYCLE = [MODE_DEFAULT, MODE_AUTO]  # only these two cycle via shift+tab

_mode = MODE_DEFAULT


def get_mode() -> str:
    return _mode


def set_mode(mode: str):
    global _mode
    if mode in (MODE_DEFAULT, MODE_AUTO, MODE_READONLY):
        _mode = mode


def cycle_mode() -> str:
    """Toggle the permission mode (shift+tab). Returns the new mode."""
    global _mode
    i = _MODE_CYCLE.index(_mode) if _mode in _MODE_CYCLE else 0
    _mode = _MODE_CYCLE[(i + 1) % len(_MODE_CYCLE)]
    return _mode


def mode_label(mode: str | None = None) -> str:
    m = mode or _mode
    if m == MODE_AUTO:
        return "⏵⏵ auto mode on (shift+tab to cycle)"
    return "⏵  confirm tool calls (shift+tab to cycle)"

_OPTIONS = [
    (OPTION_ALLOW,        "Allow once",            "run this one time"),
    (OPTION_ALLOW_ALWAYS, "Allow always",          "remember this choice"),
    (OPTION_DENY,         "Deny once",             "skip this one time"),
    (OPTION_DENY_ALWAYS,  "Deny always",           "remember this choice"),
]

# Catastrophic run_command patterns — never user-removable.
HARD_DENY_PATTERNS = [
    "rm -rf /*",
    "rm -rf /",
    "rm -rf ~",
    "rm -rf ~/*",
    ":(){ :|:& };:",
    "format *",
    "mkfs*",
    "dd if=/dev/zero of=/dev/*",
    "> /dev/sda*",
]

_rules: list[dict] | None = None


def _load_rules() -> list[dict]:
    global _rules
    if _rules is None:
        _rules = storage.load_permission_rules()
    return _rules


def _save_rules():
    storage.save_permission_rules(_rules or [])


def list_rules() -> list[dict]:
    return list(_load_rules())


def remove_rule(index: int) -> bool:
    rules = _load_rules()
    if 0 <= index < len(rules):
        rules.pop(index)
        _save_rules()
        return True
    return False


# ---------------------------------------------------------------------------
# Subject extraction + pattern matching
# ---------------------------------------------------------------------------

def _normalize_path(path: str) -> str:
    try:
        return Path(path).resolve().as_posix()
    except Exception:
        return path.replace("\\", "/")


def _subject_for(tool_name: str, args: dict) -> str:
    if tool_name in ("read_file", "write_file", "append_file", "edit_file"):
        return _normalize_path(args.get("path", ""))
    if tool_name in ("run_command", "kill_bash"):
        return str(args.get("command", args.get("bg_id", "")))
    if tool_name == "web_fetch":
        return args.get("url", "")
    if tool_name == "list_dir":
        return _normalize_path(args.get("path", "."))
    if tool_name == "glob_files":
        return _normalize_path(args.get("path", "."))
    return str(args)


def _matches(subject: str, pattern: str) -> bool:
    return fnmatch.fnmatch(subject, pattern)


def _check_hard_deny(tool_name: str, subject: str) -> bool:
    if tool_name != "run_command":
        return False
    cmd_norm = " ".join(subject.lower().split())
    for pat in HARD_DENY_PATTERNS:
        if _matches(cmd_norm, pat.lower()):
            return True
    return False


def _check_rules(tool_name: str, subject: str) -> str | None:
    """Return 'allow'/'deny' if a persisted rule matches, else None."""
    rules = _load_rules()
    deny_match = None
    allow_match = None
    for rule in rules:
        rtool = rule.get("tool", "*")
        if rtool != "*" and rtool != tool_name:
            continue
        pattern = rule.get("pattern") or "*"
        if _matches(subject, pattern):
            if rule.get("decision") == "deny":
                deny_match = "deny"
            elif rule.get("decision") == "allow":
                allow_match = "allow"
    if deny_match:
        return "deny"
    if allow_match:
        return "allow"
    return None


def _derive_always_pattern(tool_name: str, subject: str) -> str:
    if tool_name in ("run_command", "kill_bash"):
        first_token = subject.strip().split(" ")[0] if subject.strip() else "*"
        return f"{first_token} *"
    if tool_name == "web_fetch":
        try:
            parsed = urlparse(subject)
            return f"{parsed.scheme}://{parsed.netloc}/*"
        except Exception:
            return subject
    if tool_name in ("write_file", "append_file", "edit_file", "read_file", "list_dir", "glob_files"):
        return subject  # exact resolved path
    return subject


# ---------------------------------------------------------------------------
# Interactive picker (4-option)
# ---------------------------------------------------------------------------

def _getch():
    if sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            return "UP" if ch2 == "H" else "DOWN" if ch2 == "P" else "OTHER"
        if ch == "\r":
            return "ENTER"
        if ch == "\x1b":
            return "ESC"
        return ch
    else:
        import tty, termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                rest = sys.stdin.read(2)
                if rest == "[A": return "UP"
                if rest == "[B": return "DOWN"
                return "ESC"
            if ch == "\r" or ch == "\n": return "ENTER"
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _write(s: str):
    sys.stdout.write(s)
    sys.stdout.flush()


def _move_up(n: int):
    if n > 0:
        _write(f"\033[{n}A")


def _clear_line():
    _write("\033[2K\r")


def _format_args(tool_name: str, args: dict) -> str:
    if tool_name in ("run_command", "kill_bash"):
        return str(args.get("command", args.get("bg_id", "")))[:120]
    if tool_name in ("read_file", "write_file", "edit_file", "append_file"):
        return args.get("path", "")
    if tool_name in ("list_dir", "glob_files"):
        return args.get("path", ".")
    if tool_name == "grep_search":
        return f"'{args.get('pattern','')}' in {args.get('path','.')}"
    if tool_name == "web_fetch":
        return args.get("url", "")
    return str(args)[:120]


def _prompt_choice(tool_name: str, args: dict) -> str:
    from rich.console import Console
    from rich.panel import Panel
    from rich.padding import Padding
    from rich.text import Text
    from rich import box
    from . import renderer
    console = Console(highlight=False)

    desc = TOOL_DESCRIPTIONS_FALLBACK(tool_name)
    arg_preview = _format_args(tool_name, args)

    selected = 0
    NUM_OPTS = len(_OPTIONS)

    def _body():
        lines = [
            f"[{renderer.PERMISSION_BLUE} bold]{renderer.BLACK_CIRCLE} Permission requested[/{renderer.PERMISSION_BLUE} bold]",
            f"[bold]{tool_name}[/bold]  [dim]{desc}[/dim]",
            f"[dim]{arg_preview}[/dim]",
            "",
        ]
        for i, (_, label, hint) in enumerate(_OPTIONS):
            if i == selected:
                lines.append(f"[bold {renderer.PERMISSION_BLUE}]❯ {label:<16}[/bold {renderer.PERMISSION_BLUE}][dim]{hint}[/dim]")
            else:
                lines.append(f"  [dim]{label:<16}{hint}[/dim]")
        return "\n".join(lines)

    def _panel():
        return Padding(
            Panel(
                Text.from_markup(_body()),
                border_style=renderer.PERMISSION_BLUE,
                box=box.ROUNDED,
                padding=(0, 2),
            ),
            pad=(0, 0, 0, 1),
        )

    n_lines = NUM_OPTS + 6  # 4 header lines + 2 panel borders + options
    console.print()
    console.print(_panel())

    _write("\033[?25l")
    try:
        while True:
            key = _getch()
            if key == "UP":
                selected = (selected - 1) % NUM_OPTS
            elif key == "DOWN":
                selected = (selected + 1) % NUM_OPTS
            elif key == "ENTER":
                break
            elif key == "ESC" or key == "\x03":
                selected = 2  # deny once
                break
            else:
                continue

            for _ in range(n_lines):
                sys.stdout.write("\033[1A\033[2K")
            sys.stdout.flush()
            console.print(_panel())
    finally:
        _write("\033[?25h")

    choice = _OPTIONS[selected][0]
    label = _OPTIONS[selected][1]

    for _ in range(n_lines + 1):
        sys.stdout.write("\033[1A\033[2K")
    sys.stdout.flush()
    color = renderer.SUCCESS_GREEN if choice.startswith("allow") else renderer.ERROR_RED
    console.print(f"  [{color}]{renderer.BLACK_CIRCLE}[/{color}] {tool_name}  [dim]{label}[/dim]")
    console.print()

    return choice


def TOOL_DESCRIPTIONS_FALLBACK(tool_name: str) -> str:
    from .tools import TOOL_DESCRIPTIONS
    return TOOL_DESCRIPTIONS.get(tool_name, tool_name)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def ask_permission(tool_name: str, args: dict) -> str:
    """Returns OPTION_ALLOW or OPTION_DENY. Signature-compatible with agent.py call site."""
    subject = _subject_for(tool_name, args)

    if _check_hard_deny(tool_name, subject):
        from . import renderer
        renderer.print_error(f"Blocked: '{subject}' matches a hard-coded dangerous-command pattern.")
        return OPTION_DENY

    rule_decision = _check_rules(tool_name, subject)
    if rule_decision == "deny":
        return OPTION_DENY
    if rule_decision == "allow":
        return OPTION_ALLOW

    category = category_for_tool(tool_name)
    if category == "read":
        return OPTION_ALLOW

    # read-only mode: deny all write/exec/network (GUI with Agent off).
    if _mode == MODE_READONLY:
        return OPTION_DENY

    # permission mode short-circuits the interactive picker
    if _mode == MODE_AUTO:
        return OPTION_ALLOW

    choice = _prompt_choice(tool_name, args)

    if choice == OPTION_ALLOW:
        return OPTION_ALLOW
    if choice == OPTION_DENY:
        return OPTION_DENY

    pattern = _derive_always_pattern(tool_name, subject)
    rules = _load_rules()
    if choice == OPTION_ALLOW_ALWAYS:
        rules.append({"tool": tool_name, "pattern": pattern, "decision": "allow"})
        _save_rules()
        return OPTION_ALLOW
    if choice == OPTION_DENY_ALWAYS:
        rules.append({"tool": tool_name, "pattern": pattern, "decision": "deny"})
        _save_rules()
        return OPTION_DENY

    return OPTION_DENY
