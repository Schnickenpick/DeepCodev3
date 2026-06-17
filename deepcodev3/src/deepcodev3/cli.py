import sys
import io
from .chat import run


def _install_hang_dumper():
    """Press Ctrl+Break (Windows) while the app is hung to dump ALL thread
    stacks to stderr — shows exactly where it's stuck. Debug aid; harmless."""
    import faulthandler
    try:
        if sys.platform == "win32" and hasattr(faulthandler, "register"):
            import signal
            if hasattr(signal, "SIGBREAK"):
                faulthandler.register(signal.SIGBREAK, all_threads=True)
    except Exception:
        pass


def main():
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    _install_hang_dumper()
    run()


if __name__ == "__main__":
    main()
