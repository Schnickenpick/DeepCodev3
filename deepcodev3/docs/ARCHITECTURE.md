# DeepCode v3 — Architecture

Terminal coding agent. CLI talks to a hosted model gateway (`api.py` BASE URL,
34 models). One async event loop drives a persistent bottom input bar; the agent
streams output above it.

Run: `python -m deepcodev3` (from `deepcodev3/`). Entry: `__main__` → `cli.main`
→ `chat.run` → `asyncio.run(chat.main_loop())`.

## Module map (`src/deepcodev3/`)

| File | Responsibility |
|------|----------------|
| `__main__.py` / `cli.py` | Entry. `cli.main` wraps stdout in a utf-8 `TextIOWrapper` on Windows and **re-points `renderer.console.file` + `renderer._real_console_file` at it** (Console was built at import time against the original stdout). |
| `chat.py` | The REPL: `main_loop()` reads lines from the InputController, dispatches commands / quiz phase / agent / chat / reasoning / ultracode, persists sessions + memory. Also `/color` (live accent), and `_bootstrap_context` / `_seed_dir_listing` (auto-context scan on start + `/compact`). Biggest file (~1500 lines). |
| `input_loop.py` | `InputController` — the persistent bottom input bar (see UI_GUIDE.md). Owns the prompt_toolkit Application, the pending-message queue, interrupt + pause/resume + permission-mode-toggle wiring. |
| `agent.py` | `run_agent()` — the tool-use loop. Streams a model response, parses `<tool>{...}</tool>` calls, asks permission, executes via `TOOL_REGISTRY`, appends tool results, repeats until a plain-text response. `swarm_mode=True` silences all console output (UltraCode workers). `on_event` callback surfaces live progress. |
| `tools.py` | Tool implementations + `TOOL_REGISTRY`. read/write/append/edit_file, glob, grep (ripgrep or python fallback), run_command (+ bg), list_dir, web_fetch. |
| `tool_schema.py` | `category_for_tool()` → `read` / `write` / `exec` / `network`. Used by permissions. |
| `permissions.py` | `ask_permission(tool, args)` → allow/deny. Hard-deny patterns, persisted allow/deny rules, the interactive 4-option picker, and **permission modes** (`confirm tool calls` ⇄ `auto mode on`, toggled with shift+tab). |
| `reasoning.py` | `run_reasoning(task, model, level, ...)` — multi-stage think/critique/plan/refine before answering. Levels: low/middle/high/ultra. `quiet=True` suppresses all stdout (required in swarm). |
| `ultracode.py` | UltraCode swarm: leader → managers → workers DAG. Per-group reasoning level + single-file discipline + `allowed_files`. `run_dag` schedules via `asyncio.wait(FIRST_COMPLETED)` (no shared Event — that caused a deadlock). |
| `workflows_ui.py` | `/workflows` full-screen TUI to watch a running swarm. |
| `renderer.py` | All rich output. `console`, `print_*`, `stream_token`/`finish_stream`. Holds the console-file routing state (see below), the live accent (`CLAUDE_ORANGE`/`set_accent`/`resolve_color`/`ACCENT_PRESETS`), and the `DEEPCODE_LOGO` art. |
| `storage.py` | Config, history, memory (.md) files. `load_config`/`save_config`, sessions, MEMORY/USER/PROJECT/DEEPCODE/SOUL md. |
| `models.py` | Model registry, `get_model`, `DEFAULT_MODEL`, provider/tier colors. |
| `system_prompt.py` | `SYSTEM_PROMPT` — tool docs + rules injected into every agent turn. |
| `api.py` | `stream_chat`/`stream_merge`/`stream_search` SSE generators against the gateway. `_stream_endpoint` retries transient network errors (DNS/connect/read) with backoff — but only before the first chunk is yielded. |

## Startup / post-compact: auto-context bootstrap

On a fresh session (and after `/compact`), `main_loop` runs `_run_bootstrap()`
before the input loop if `cfg["auto_context"]` is on (default True):

- `_seed_dir_listing()` does a cheap deterministic `os.scandir(".")` (dirs first,
  names + sizes, common junk like `.git`/`node_modules`/`__pycache__` skipped).
- `_bootstrap_context()` hands that seed to `run_agent` as a one-shot task: the
  MODEL decides which files matter (by name + size), can `list_dir` deeper into
  subdirs (`docs/`, `src/`), `read_file`s selectively, then returns a ≤200-word
  orientation note. Read-category tools auto-allow, so no permission prompts.
- The note is injected as an `assistant` turn into `agent_conversation` +
  `current_session` (summary-only — raw file reads are NOT kept) and shown in a
  "Project context" panel. Wrapped in `_run_cancelable`, so Esc aborts the scan.
- `/context` re-runs it on demand; `/context on|off` toggles the auto-scan.

## Theming / accent color (`/color`)

`renderer.CLAUDE_ORANGE` is the LIVE accent, read at call time everywhere
(including `renderer.ACCENT`, agent.py's `renderer.CLAUDE_ORANGE`). `set_accent(rgb)`
reassigns it via `global`, so reassigning recolors the whole UI in place.
`resolve_color()` maps a preset name / `#hex` / `rgb(r,g,b)` to a rich rgb string;
`ACCENT_PRESETS` holds the named ones (blue = the light blue, orange = default).
`/color` also calls `controller.set_accent(accent_hex())` to recolor the input
box — the prompt_toolkit side wants `#rrggbb` (`accent_hex()`), and the box's
frame border + `❯` prompt symbol + completion highlight rebuild from it via
`InputController._make_style()`. Persisted as `cfg["accent"]`, applied before the
banner so the logo (`renderer.DEEPCODE_LOGO`, a terminal-shell `❯_` mark) renders
in the chosen color.

## The agent turn (request → tools → response)

1. `main_loop` gets a line from `InputController.get_line()`.
2. `_run_quiz_phase` does ONE `stream_chat` first — if the model emits a `<quiz>`
   it asks clarifying questions; otherwise it returns the answer (prefetched).
3. Agent mode: `_run_cancelable(run_agent(...))`. `run_agent` loops:
   stream response → `_parse_tool_calls` → for each call `ask_permission` then
   `TOOL_REGISTRY[name](args)` → append `tool_result` → continue. Plain-text
   response (no tool call) ends the loop and is returned.
4. `_run_cancelable` registers `task.cancel` as the InputController's interrupt
   callback (Esc on an empty box cancels the turn) and runs `begin_turn`/
   `end_turn` for the live status line.

## Console-file routing (read before touching output)

`renderer.console` (rich) is the single output channel. Its `.file` is swapped
between three targets — getting this wrong makes output vanish or corrupt:

- `renderer._real_console_file` — the actual terminal stdout. **Restore target.**
- `renderer._buffer_file` — a black-hole buffer used while a full-screen TUI
  (`/workflows`) owns the alt-screen; flushed when the TUI closes.
- the prompt_toolkit `StdoutProxy` (`sys.stdout` while `patch_stdout` is active)
  — routes prints ABOVE the live input bar.

Rules:
- `InputController.start()` enters `patch_stdout(raw=True)` for the WHOLE session
  and points `console.file` at the proxy. `stop()` restores `_real_console_file`.
- NEVER restore `console.file` to "whatever it was before" — a stale dead
  `StdoutProxy` sends every print into the void (this was a real bug: agent
  replied but nothing showed). Always restore to a known-real handle.
- `raw=True` is required so rich's ANSI escapes pass through untouched.

## Things that have bitten us (don't repeat)

- **Windows cp1252 console** can't encode `⏳ … ✓ ✗ ⏵`. A raw `sys.stdout.write`
  of those raises `UnicodeEncodeError` and can kill a whole code path. `cli.py`
  wraps stdout utf-8; status writers (`reasoning._safe_write`) swallow encode
  errors. rich handles encoding itself.
- **Two things reading the keyboard at once** deadlock/garble. The persistent
  input thread owns the keyboard; blocking msvcrt pickers must `pause()` it
  first (see `_keyboard_for_picker`).
- **`asyncio.Event` clear/set race** in a DAG scheduler can deadlock when a task
  finishes between `clear()` and `wait()`. Await the tasks directly instead.
- **Module loaded from the wrong path**: `DeepCodev2/src` is also on `sys.path`.
  Verify `import deepcodev3; deepcodev3.__file__` points at v3 src.
- **False "truncated mid tool-call"**: `agent.py` detects an unclosed `<tool>`
  to retry cut-off calls. A plain `"<tool>" in full_response` check fired on
  PROSE that merely mentions `<tool>` (e.g. the auto-context note describing
  agent.py's tool format), chopping a valid reply + spamming the retry error.
  The guard now also requires `<tool>` be followed by `{` (a JSON open) AND not
  sit inside an inline-code backtick span (odd backtick count before it).
  Lesson: any text the MODEL writes can contain our own control tokens — gate
  control-token detection on structure, not bare substring presence.

## Git

Repo root is `DeepCodev3/` (one level above `deepcodev3/`). Trunk branch is
`master`. `.gitignore` excludes `__pycache__`, `*.log`, scratch dirs.
