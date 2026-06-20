<div align="center">

# DeepCode v3

[![Discord](https://img.shields.io/discord/0?label=Discord&logo=discord&logoColor=white&color=5865F2)](https://discord.gg/cgVa2rqWKv)

A Claude-Code-style terminal coding agent, written in Python. Streams
responses, runs file/shell tools behind a permission layer, multi-stage
reasoning, an UltraCode agent swarm, and a desktop GUI.

**[Join the Discord](https://discord.gg/cgVa2rqWKv)** — support, updates, bug reports.

</div>

---

## DISCLAIMER

I am **NOT** responsible for what you do with this tool, or what this tool
does with you. Use at your own risk.

This is a coding agent, not a jailbreak tool — see [Heads up](#heads-up-please-read)
below before asking for one.

---

## Install

Easiest: grab the latest build from
[Releases](https://github.com/Schnickenpick/DeepCodev3/releases).

Or from source, from this directory (`deepcodev3/`):

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

After `/exit`, DeepCode prints the command to resume that exact chat.

## Commands

DeepCode has a full slash-command system — type `/` to see them, or `/help`
at any time. These are not optional extras; most day-to-day control over the
agent happens through them:

| Command | What it does |
|---|---|
| `/help`, `/?` | Show all commands |
| `/agent` | Toggle agent mode (file/shell tools) |
| `/model` | Switch model — e.g. `/model opus` |
| `/models` | List all 34 available models |
| `/reasoning` | Set reasoning level — `off`/`low`/`middle`/`high`/`ultra` |
| `/ultracode <task>` | Spawn a hierarchical agent swarm (auto-sized) — e.g. `/ultracode build a REST API` |
| `/workflows` | View running/recent UltraCode swarms |
| `/plan` | Plan a task step-by-step, then execute or refine |
| `/context` | Scan the project for context — `/context on|off` to toggle auto-scan |
| `/init` | Generate `DEEPCODE.md` for the current project |
| `/memory` | Show remembered facts |
| `/permissions` | View/manage saved permission rules |
| `/session` | Browse & resume past conversations |
| `/new` | Start a new conversation |
| `/compact` | Summarize the conversation to save context |
| `/history` | Quick list of past conversations |
| `/color` ` <name/#hex>` | Theme the UI accent color |
| `/notify` | Toggle the bell notification |
| `/merge` | Toggle Merge AI mode |
| `/search` | Toggle Web Search mode |
| `/soul` | View/generate/reset DeepCode's personality (`SOUL.md`) |
| `/keybinds` | Show all keyboard shortcuts |
| `/quizmaxoptions <n>` | Set max quiz options |
| `/clear` | Clear the screen |
| `/exit` | Quit |

Other things worth knowing:

- `@path` — attach a file's contents to your message
- **shift+tab** — toggle permission mode (confirm ⇄ auto)

## Heads up, please read

DeepCode is a coding assistant. It is **not** designed to roleplay, bypass
any safety behavior, or act as a general-purpose "jailbreak" tool, If you want a jailbreak look in the discord jailbreak channel, dont annoy staff or me. If
you've got a feature request or you think the agent is behaving wrong on a
*coding* task, that's exactly what the [Discord](https://discord.gg/cgVa2rqWKv)
and the issue tracker are for.

## Desktop GUI

An Electron + React desktop app lives in `../app`, backed by the websocket
bridge in `../server`. In short:

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

Produces two self-contained executables in `dist/` (no Python or Node needed
to run them):

- `dist/DeepCodeCLI.exe` — the terminal app
- `dist/DeepCodeGUI.exe` — the desktop GUI (a single portable exe; bundles
  the Python backend)

Requires PyInstaller and the GUI deps installed (`cd app && npm install`).

## Notes

- Configured against a hosted model gateway (`src/deepcodev3/api.py`).
- Windows-focused (developed on Windows Terminal + PowerShell); the TUI uses
  a real console buffer and won't run headless.
- See `deepcodev3/docs/ARCHITECTURE.md` and `deepcodev3/docs/UI_GUIDE.md` for
  internals.

## Support

- **Discord:** https://discord.gg/cgVa2rqWKv
- **Issues:** open one on this repo
