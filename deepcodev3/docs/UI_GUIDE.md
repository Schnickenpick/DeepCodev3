# DeepCode v3 — UI / Terminal Editing Guide

Read this before changing anything in `input_loop.py`, `renderer.py`, or the
output paths in `agent.py` / `reasoning.py`. The UI is a prompt_toolkit +
rich + raw-stdout mix on a Windows console; the failure modes are non-obvious
and cost real time. Test changes live in PowerShell — they cannot be reproduced
headless (see "Testing").

## The bottom input bar (`input_loop.InputController`)

Claude-Code-style: a bordered input box pinned at the bottom with a mode line
beneath it; agent output scrolls above; you can type/queue the next message
while a turn streams.

How it works:
- A small **non-full-screen** prompt_toolkit `Application`: `Frame(TextArea)` +
  a 1-line `Window` mode line, inside a `FloatContainer` (so the completion
  menu can pop over the box). Built lazily in `start()` — NOT in `__init__`
  (constructing an Application touches the Win32 console and raises in
  non-console contexts).
- `patch_stdout(raw=True)` is held for the whole session. Rich writes to stdout
  land ABOVE the bar; the bar redraws under them. This is why output "scrolls
  above" the pinned input.
- The Application runs in a **daemon thread** (`_run`). Each submitted line is
  appended to `self.pending` (lock-guarded). The async loop pops FIFO via
  `get_line()`. Running input in its own thread is what enables type-ahead.

Key bindings (in `_build_app`):
- `enter` → submit (exit the app with the text). `c-j` → newline.
- `escape` (empty box) → `fire_interrupt()` (cancels the running turn).
- `up` (empty box, queue non-empty) → pull the most recent queued message back
  into the box to edit; otherwise normal cursor/history nav.
- `s-tab` → `mode_cycle_cb()` (permission mode toggle).

Box height: `_input_height` returns `Dimension(preferred==max==line_count,
min=1)`. NO slack/`max=N` reservation — that made the box render tall/fill the
screen. It must equal the content line count so the box is 1 line by default
and grows downward per newline (capped 10).

## Console-file routing — the #1 source of "output vanished" bugs

See ARCHITECTURE.md "Console-file routing". The short version:
- All rich output goes through `renderer.console`. Its `.file` is the routing
  switch.
- The ONLY safe restore targets are `renderer._real_console_file` (terminal) or
  `renderer._buffer_file` (when `is_tui_active()`). Restoring a captured
  "previous" value can resurrect a dead `StdoutProxy` → silent void.
- If you add a code path that swaps `console.file`, restore it in a `finally`
  to a known-real handle, never to a saved snapshot.

## Raw `\r` spinners vs the bottom bar

Any code that writes a `\r`-overwrite spinner straight to `sys.stdout`
(`agent._thinking_dots`, `reasoning._status`) will draw ON the input box, not
above it. Gate ALL such writes on:

```python
if not renderer.is_tui_active() and not swarm_mode and not renderer.is_bottom_bar_active():
```

`renderer.set_bottom_bar_active(True/False)` is set by `InputController.start/
stop`. While the bar is active, show progress in the **mode line**
(`status_suffix()`: live tokens + elapsed + "Esc to interrupt") instead of a
spinner.

## Keyboard contention — pause the input thread for pickers

There is exactly one keyboard. The persistent input thread reads it continuously
via `app.run()`. Any other blocking reader (the msvcrt arrow-key pickers:
`_pick_option`, `_model_picker`, `_session_browser`, the permission picker)
MUST take the keyboard exclusively:

```python
with _keyboard_for_picker():   # pauses + resumes the InputController
    ... _getch() loop ...
```

`pause()` sets a flag and calls `app.exit()` to break the thread out of
`app.run()`, then waits for it to park. `resume()` clears the flag; the thread
loops back into `app.run()`. Without this, two readers fight → swallowed keys,
garbled render, or a hang.

One-off TEXT prompts (quiz free-text, plan-refine) do NOT open a second prompt.
They read the next line from the SAME bar via `_INPUT_CONTROLLER.read_one()`.

## Windows console encoding

cp1252 can't encode `⏳ … ✓ ✗ ⏵ ⏵⏵ 🧠`. Consequences:
- `cli.main` wraps stdout/stderr in utf-8 `TextIOWrapper` (errors="replace").
- Raw status writers swallow encode errors (`reasoning._safe_write`).
- rich (`renderer.console`) encodes safely on its own — prefer it over raw
  `sys.stdout.write` for anything with unicode.
- A non-full-screen Application still needs a real Win32 console buffer. Windows
  Terminal + PowerShell has one. A bare pipe / xterm-in-sandbox does NOT →
  `NoConsoleScreenBufferError`. That's why UI changes can't be tested headless.

## prompt_toolkit gotchas specific to this project

- Do NOT use `full_screen=True` or force `Vt100_Output` on Win32 — both break
  (`NoConsoleScreenBufferError` / garbled offset). `/workflows` uses a real
  full-screen Application and is the documented exception; the input bar is
  deliberately non-full-screen.
- `refresh_interval=0.5` on the input Application keeps the mode line live
  (elapsed timer, queued count) and helps clear resize artifacts.
- Resizing the terminal SMALLER can leave stale box-border fragments in the
  scrollback (pt erases against the old width). Largely cosmetic; clears on the
  next full repaint / `/clear`.

## Testing UI changes

1. `python -c "import ast; ast.parse(open(...).read())"` to catch syntax.
2. Import the module to catch import errors. Building the Application headless
   will raise `NoConsoleScreenBufferError` — that's EXPECTED, not your bug.
3. Logic (queue order, interrupt callback, mode cycle) can be unit-tested with a
   fake session / direct calls.
4. Visual behavior MUST be verified live in PowerShell: `python -m deepcodev3`.
   When chasing an invisible-output / hang bug, add a debug logger that writes
   to an ABSOLUTE path (`~/deepcode_debug.log`) — a cwd-relative log can land
   somewhere you don't expect, and stdout is owned by the bar.
