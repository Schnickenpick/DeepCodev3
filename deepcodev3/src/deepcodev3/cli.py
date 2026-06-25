from __future__ import annotations
import os
import sys
import io
from .chat import run

_HELP = """\
DeepCode v3 — terminal coding agent

Usage:
  deepcode                    start a new conversation
  deepcode -c, --continue     resume your most recent conversation
  deepcode --resume <id>      resume a specific conversation (full or prefix id)
  deepcode --resume           browse and pick a conversation to resume
  deepcode --base-url <url>   use this backend for the session (overrides /server config)
  deepcode --api-key <key>    API key to send with --base-url
  deepcode --version          print version
  deepcode --help             show this help

In-app: type /help for commands, /server to view/change the backend, shift+tab to toggle permission mode.
"""


def _pop_flag_value(argv: list[str], *names: str) -> str | None:
    for name in names:
        if name in argv:
            i = argv.index(name)
            if i + 1 < len(argv):
                val = argv[i + 1]
                del argv[i:i + 2]
                return val
            del argv[i]
    return None


def main():
    argv = sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print(_HELP)
        return
    if any(a in ("-V", "--version") for a in argv):
        from . import __version__
        print(f"DeepCode v{__version__}")
        return

    # chat.run() re-parses sys.argv itself (for -c/--resume), so pop these
    # flags from the real argv, not just the local copy.
    base_url = _pop_flag_value(sys.argv, "--base-url")
    api_key = _pop_flag_value(sys.argv, "--api-key")
    if base_url:
        os.environ["DEEPCODE_BASE_URL"] = base_url
    if api_key:
        os.environ["DEEPCODE_API_KEY"] = api_key

    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        # renderer.console was constructed at import time bound to the ORIGINAL
        # sys.stdout. Re-point it (and the "real file" reference used to restore
        # after patch_stdout) at the freshly wrapped utf-8 stdout.
        from . import renderer
        renderer.console.file = sys.stdout
        renderer._real_console_file = sys.stdout
    run()


if __name__ == "__main__":
    main()
