import sys
import io
from .chat import run


def main():
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
