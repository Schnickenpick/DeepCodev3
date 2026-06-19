# DeepCode v3

A claude-code-style terminal coding agent in Python. Streams responses, runs
file/shell tools with a permission layer, multi-stage reasoning, and an
UltraCode agent swarm — plus a desktop GUI.

## Install

From this directory (`deepcodev3/`):

```sh
pip install -e .
```

This puts a `deepcode` command on your PATH.

## Run

```sh
deepcode                 # new conversation
deepcode -c              # resume the most recent conversation
deepcode --resume <id>   # resume a specific conversation
deepcode --resume        # browse + pick a conversation
deepcode --help
```

In-app:

- `/help` — list commands
- `/agent` — toggle file/shell tools
- `/reasoning off|low|middle|high|ultra`
- `/model` — switch model (34 available)
- `/color <name|#hex>` — theme the UI
- `/ultracode <task>` — spawn a hierarchical agent swarm
- `@path` — attach a file's contents to your message
- **shift+tab** — toggle permission mode (confirm ⇄ auto)

After you `/exit`, DeepCode prints the command to resume that exact chat.

## Desktop GUI

An Electron + React desktop app lives in `../app`, backed by the websocket
bridge in `../server`. See those directories. In short:

```sh
# from repo root
cd app && npm install && npm run dev
```

The GUI shares conversation history with the terminal.

## Building standalone exes

From the repo root (one level up from this `deepcodev3/`):

```sh
python build_all.py
```

Produces two self-contained executables in `dist/` (no Python or Node needed to
run them):

- `dist/DeepCodeCLI.exe` — the terminal app
- `dist/DeepCodeGUI.exe` — the desktop GUI (a single portable exe; bundles the
  Python backend)

Requires PyInstaller and the GUI deps installed (`cd app && npm install`).

## Notes

- Configured against a hosted model gateway (`src/deepcodev3/api.py`).
- Windows-focused (developed on Windows Terminal + PowerShell); the TUI uses a
  real console buffer and won't run headless.
- See `docs/ARCHITECTURE.md` and `docs/UI_GUIDE.md` for internals.
