import sys
import io
from .chat import run

_HELP = """\
DeepCode v3 — terminal coding agent

Usage:
  deepcode                 start a new conversation
  deepcode -c, --continue  resume your most recent conversation
  deepcode --resume <id>   resume a specific conversation (full or prefix id)
  deepcode --resume        browse and pick a conversation to resume
  deepcode --version       print version
  deepcode --help          show this help

In-app: type /help for commands, shift+tab to toggle permission mode.
"""


def main():
    argv = sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print(_HELP)
        return
    if any(a in ("-V", "--version") for a in argv):
        from . import __version__
        print(f"DeepCode v{__version__}")
        return

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
