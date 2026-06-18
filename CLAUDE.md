# DeepCode v3 — working notes for Claude

DeepCode v3 is a terminal coding agent (Python, prompt_toolkit + rich) that
talks to a hosted model gateway. Source lives in `deepcodev3/src/deepcodev3/`.
Run it with `python -m deepcodev3` from `deepcodev3/`.

## Read these first
- `deepcodev3/docs/ARCHITECTURE.md` — module map, the agent turn, console-file
  routing, and the bugs that have already bitten us. Read before changing any
  cross-module behavior.
- `deepcodev3/docs/UI_GUIDE.md` — REQUIRED before editing `input_loop.py`,
  `renderer.py`, or output paths in `agent.py` / `reasoning.py`. The terminal
  UI has non-obvious failure modes (console-file routing, raw-`\r` spinners,
  keyboard contention, Windows cp1252 encoding, prompt_toolkit Win32 quirks).

## Hard-won rules (don't relearn the hard way)
- All output goes through `renderer.console`; never restore `console.file` to a
  saved "previous" value — only to `_real_console_file` (or `_buffer_file` when
  a full-screen TUI is active). A stale `StdoutProxy` = invisible output.
- Gate raw `\r` spinners on `not renderer.is_bottom_bar_active()`.
- One keyboard: blocking msvcrt pickers must `pause()` the input thread
  (`_keyboard_for_picker`). One-off text prompts use `controller.read_one()`.
- UI changes can't be tested headless (`NoConsoleScreenBufferError`); verify
  live in PowerShell. Syntax/import/logic CAN be tested headless.
- cp1252 can't encode `⏳ ✓ ✗ ⏵ …` — prefer rich; raw writers must swallow
  encode errors.

## Git
Repo root is `DeepCodev3/`; trunk branch is `master`. Commit when asked.
