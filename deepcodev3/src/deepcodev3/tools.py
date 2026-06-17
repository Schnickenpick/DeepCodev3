from __future__ import annotations
import fnmatch
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

NOISE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".egg-info"}


class ToolError(Exception):
    pass


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

MAX_READ_LINES = 2000
MAX_READ_CHARS = 50_000


def read_file(path: str, offset: int | None = None, limit: int | None = None) -> str:
    p = Path(path)
    if not p.exists():
        raise ToolError(f"File not found: {path}")
    if not p.is_file():
        raise ToolError(f"Not a file: {path}")
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise ToolError(str(e))

    lines = content.splitlines()
    total = len(lines)

    start = max(0, (offset - 1)) if offset else 0
    end = start + limit if limit else min(total, start + MAX_READ_LINES)
    end = min(end, total)

    selected = lines[start:end]
    numbered = "\n".join(f"{i+1:4} | {line}" for i, line in enumerate(selected, start=start))

    if len(numbered) > MAX_READ_CHARS:
        numbered = numbered[:MAX_READ_CHARS] + "\n... [truncated]"

    note = ""
    if end < total:
        note = f"\n... [{total - end} more lines, use offset={end+1} to continue]"

    return f"File: {path} ({total} lines, showing {start+1}-{end}){note}\n\n{numbered}"


# ---------------------------------------------------------------------------
# write_file / append_file
# ---------------------------------------------------------------------------

MAX_WRITE_CHARS = 120_000  # ~30k tokens, well under proxy limits


def write_file(path: str, content: str) -> str:
    if len(content) > MAX_WRITE_CHARS:
        raise ToolError(
            f"Content too large ({len(content):,} chars). Max is {MAX_WRITE_CHARS:,} chars (~30k tokens). "
            "Split into multiple write_file + append_file calls."
        )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    lines = content.count("\n") + 1
    return f"Written {lines} lines to {path}"


def append_file(path: str, content: str) -> str:
    if len(content) > MAX_WRITE_CHARS:
        raise ToolError(
            f"Content too large ({len(content):,} chars). Max is {MAX_WRITE_CHARS:,} chars. Split further."
        )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(content)
    lines = content.count("\n") + 1
    return f"Appended {lines} lines to {path}"


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    p = Path(path)
    if not p.exists():
        raise ToolError(f"File not found: {path}")
    if old_string == new_string:
        raise ToolError("old_string and new_string are identical — no-op")
    content = p.read_text(encoding="utf-8", errors="replace")
    if old_string not in content:
        raise ToolError(f"old_string not found in {path}. Read the file first to get exact content.")
    count = content.count(old_string)
    if count > 1 and not replace_all:
        raise ToolError(
            f"old_string matches {count} places in {path}. "
            "Add more surrounding context to make it unique, or pass replace_all=true to replace all occurrences."
        )
    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)
    p.write_text(new_content, encoding="utf-8")
    return f"Edited {path} — replaced {count if replace_all else 1} occurrence(s)"


# ---------------------------------------------------------------------------
# glob_files
# ---------------------------------------------------------------------------

MAX_GLOB_RESULTS = 200


def _is_noise(path: Path) -> bool:
    return any(part in NOISE_DIRS for part in path.parts)


def glob_files(pattern: str, path: str = ".") -> str:
    base = Path(path)
    if not base.exists():
        raise ToolError(f"Path not found: {path}")

    matches = [Path(m) for m in base.glob(pattern)]
    matches = [m for m in matches if m.is_file() and not _is_noise(m)]

    matches.sort(key=lambda m: m.stat().st_mtime, reverse=True)

    truncated = len(matches) > MAX_GLOB_RESULTS
    matches = matches[:MAX_GLOB_RESULTS]

    if not matches:
        return f"No files match '{pattern}' in {path}/"

    lines = [str(m) for m in matches]
    out = "\n".join(lines)
    if truncated:
        out += f"\n... [truncated to {MAX_GLOB_RESULTS} results]"
    return out


# ---------------------------------------------------------------------------
# grep_search
# ---------------------------------------------------------------------------

_RG_PATH: str | None | bool = None  # None = not checked yet, False = not found

DEFAULT_HEAD_LIMIT = 250
MAX_GREP_FILES = 5000
MAX_LINE_CHARS = 500

TYPE_EXT_MAP = {
    "py": [".py"],
    "js": [".js", ".jsx", ".mjs", ".cjs"],
    "ts": [".ts", ".tsx"],
    "go": [".go"],
    "rust": [".rs"],
    "java": [".java"],
    "c": [".c", ".h"],
    "cpp": [".cpp", ".cc", ".cxx", ".hpp", ".h"],
    "md": [".md"],
    "json": [".json"],
    "html": [".html", ".htm"],
    "css": [".css", ".scss"],
    "yaml": [".yaml", ".yml"],
    "sh": [".sh", ".bash"],
}


def _find_rg() -> str | None:
    global _RG_PATH
    if _RG_PATH is None:
        _RG_PATH = shutil.which("rg") or False
        if _RG_PATH is False:
            from . import renderer
            renderer.print_info(
                "Note: ripgrep ('rg') not found on PATH — using a slower pure-Python search fallback. "
                "Install ripgrep for faster grep_search."
            )
    return _RG_PATH or None


def _apply_head_limit(lines: list[str], head_limit: int | None, offset: int = 0) -> tuple[list[str], bool]:
    if head_limit is None:
        head_limit = DEFAULT_HEAD_LIMIT
    sliced = lines[offset:]
    if head_limit == 0:
        return sliced, False
    truncated = len(sliced) > head_limit
    return sliced[:head_limit], truncated


def _grep_with_rg(rg: str, pattern: str, path: str, glob: str | None, type_: str | None,
                   output_mode: str, context_before: int | None, context_after: int | None,
                   context: int | None, show_line_numbers: bool, case_insensitive: bool,
                   multiline: bool) -> list[str]:
    args = [rg, "--hidden", "--glob", "!.git/*"]
    for d in NOISE_DIRS:
        args += ["--glob", f"!{d}/*"]
    args.append("--max-columns")
    args.append(str(MAX_LINE_CHARS))

    if case_insensitive:
        args.append("-i")
    if multiline:
        args += ["-U", "--multiline-dotall"]
    if glob:
        args += ["--glob", glob]
    if type_:
        args += ["--type", type_]

    if output_mode == "files_with_matches":
        args.append("-l")
    elif output_mode == "count":
        args.append("-c")
    else:
        if show_line_numbers:
            args.append("-n")
        if context is not None:
            args += ["-C", str(context)]
        else:
            if context_before is not None:
                args += ["-B", str(context_before)]
            if context_after is not None:
                args += ["-A", str(context_after)]

    if pattern.startswith("-"):
        args += ["-e", pattern]
    else:
        args.append(pattern)
    args.append(path)

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise ToolError("grep_search timed out after 60s")

    if result.returncode not in (0, 1):
        raise ToolError(result.stderr.strip() or "ripgrep error")

    return [l for l in result.stdout.splitlines() if l]


def _iter_files(base: Path, glob: str | None, type_: str | None):
    exts = TYPE_EXT_MAP.get(type_) if type_ else None
    count = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in NOISE_DIRS]
        for name in files:
            p = Path(root) / name
            if glob and not fnmatch.fnmatch(name, glob):
                continue
            if exts and p.suffix not in exts:
                continue
            count += 1
            if count > MAX_GREP_FILES:
                return
            yield p


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:1024]


def _grep_with_python(pattern: str, path: str, glob: str | None, type_: str | None,
                       output_mode: str, context_before: int | None, context_after: int | None,
                       context: int | None, show_line_numbers: bool, case_insensitive: bool,
                       multiline: bool) -> list[str]:
    base = Path(path)
    flags = re.IGNORECASE if case_insensitive else 0
    if multiline:
        flags |= re.DOTALL | re.MULTILINE
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        raise ToolError(f"Invalid regex: {e}")

    ctx_before = context if context is not None else (context_before or 0)
    ctx_after = context if context is not None else (context_after or 0)

    out: list[str] = []
    for p in _iter_files(base, glob, type_):
        try:
            raw = p.read_bytes()
        except Exception:
            continue
        if _is_binary(raw):
            continue
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            continue

        lines = text.splitlines()
        matched_lines = [i for i, line in enumerate(lines) if regex.search(line)]
        if not matched_lines:
            continue

        if output_mode == "files_with_matches":
            out.append(str(p))
            continue
        if output_mode == "count":
            out.append(f"{p}:{len(matched_lines)}")
            continue

        emitted = set()
        for i in matched_lines:
            for j in range(max(0, i - ctx_before), min(len(lines), i + ctx_after + 1)):
                if j in emitted:
                    continue
                emitted.add(j)
                line_text = lines[j][:MAX_LINE_CHARS]
                if show_line_numbers:
                    out.append(f"{p}:{j+1}: {line_text}")
                else:
                    out.append(f"{p}: {line_text}")

    return out


def grep_search(pattern: str, path: str = ".", glob: str | None = None, type: str | None = None,
                 output_mode: str = "files_with_matches",
                 context_before: int | None = None, context_after: int | None = None,
                 context: int | None = None,
                 show_line_numbers: bool = True, case_insensitive: bool = False,
                 head_limit: int | None = None, offset: int = 0,
                 multiline: bool = False) -> str:
    base = Path(path)
    if not base.exists():
        raise ToolError(f"Path not found: {path}")
    if output_mode not in ("content", "files_with_matches", "count"):
        raise ToolError(f"Invalid output_mode: {output_mode}")

    rg = _find_rg()
    if rg:
        lines = _grep_with_rg(rg, pattern, path, glob, type, output_mode,
                               context_before, context_after, context,
                               show_line_numbers, case_insensitive, multiline)
    else:
        lines = _grep_with_python(pattern, path, glob, type, output_mode,
                                   context_before, context_after, context,
                                   show_line_numbers, case_insensitive, multiline)

    if not lines:
        return f"No matches for '{pattern}' in {path}"

    sliced, truncated = _apply_head_limit(lines, head_limit, offset)
    result = "\n".join(sliced)
    if truncated:
        result += f"\n... [{len(lines) - offset - len(sliced)} more results, use offset to page]"
    return result


# ---------------------------------------------------------------------------
# run_command / bash_output / kill_bash
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600

_BACKGROUND_PROCS: dict[str, dict] = {}
_BG_COUNTER = 0


def run_command(command: str, description: str | None = None, timeout: int | None = None,
                 run_in_background: bool = False, cwd: str | None = None) -> str:
    global _BG_COUNTER
    t = min(timeout or DEFAULT_TIMEOUT, MAX_TIMEOUT)
    workdir = cwd or os.getcwd()

    if run_in_background:
        _BG_COUNTER += 1
        bg_id = f"bg_{_BG_COUNTER}"
        out_file = tempfile.NamedTemporaryFile(prefix=f"deepcodev3_{bg_id}_", suffix=".log", delete=False)
        out_path = out_file.name
        out_file.close()
        f = open(out_path, "w", encoding="utf-8")

        popen_kwargs = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        proc = subprocess.Popen(
            command, shell=True, cwd=workdir,
            stdout=f, stderr=subprocess.STDOUT, text=True, **popen_kwargs,
        )
        _BACKGROUND_PROCS[bg_id] = {"proc": proc, "out_path": out_path, "file": f, "command": command}
        return f"Started in background: {bg_id}\nUse bash_output({{\"bg_id\": \"{bg_id}\"}}) to check progress."

    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=t, cwd=workdir,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]\n{err}")
        if result.returncode != 0:
            parts.append(f"[exit code: {result.returncode}]")
        return "\n".join(parts) if parts else "(no output)"
    except subprocess.TimeoutExpired:
        raise ToolError(f"Command timed out after {t}s")
    except Exception as e:
        raise ToolError(str(e))


def bash_output(bg_id: str) -> str:
    entry = _BACKGROUND_PROCS.get(bg_id)
    if not entry:
        raise ToolError(f"Unknown background task: {bg_id}")
    proc = entry["proc"]
    running = proc.poll() is None

    try:
        entry["file"].flush()
    except Exception:
        pass

    try:
        content = Path(entry["out_path"]).read_text(encoding="utf-8", errors="replace")
    except Exception:
        content = ""

    status = "running" if running else f"exited (code {proc.returncode})"
    if len(content) > MAX_READ_CHARS:
        content = content[-MAX_READ_CHARS:]
        content = "... [truncated, showing tail]\n" + content
    return f"[{bg_id}] {entry['command']}\nStatus: {status}\n\n{content or '(no output yet)'}"


def kill_bash(bg_id: str) -> str:
    entry = _BACKGROUND_PROCS.get(bg_id)
    if not entry:
        raise ToolError(f"Unknown background task: {bg_id}")
    proc = entry["proc"]
    if proc.poll() is not None:
        return f"[{bg_id}] already exited (code {proc.returncode})"

    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    try:
        entry["file"].close()
    except Exception:
        pass
    return f"[{bg_id}] killed"


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

def list_dir(path: str = ".") -> str:
    p = Path(path)
    if not p.exists():
        raise ToolError(f"Path not found: {path}")
    if not p.is_dir():
        raise ToolError(f"Not a directory: {path}")
    entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    lines = []
    for e in entries:
        if e.is_dir():
            lines.append(f"  📁 {e.name}/")
        else:
            size = e.stat().st_size
            size_str = f"{size:,}" if size < 1024 else f"{size//1024:,}KB"
            lines.append(f"  📄 {e.name}  [{size_str}]")
    return f"{path}/\n" + "\n".join(lines) if lines else f"{path}/ (empty)"


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------

MAX_FETCH_CHARS = 15_000


class _TextExtractor:
    """Minimal HTML-to-text extractor using stdlib HTMLParser."""

    def __init__(self):
        from html.parser import HTMLParser

        class _Inner(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text_parts: list[str] = []
                self._skip_depth = 0

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "noscript"):
                    self._skip_depth += 1

            def handle_endtag(self, tag):
                if tag in ("script", "style", "noscript") and self._skip_depth > 0:
                    self._skip_depth -= 1

            def handle_data(self, data):
                if self._skip_depth == 0:
                    stripped = data.strip()
                    if stripped:
                        self.text_parts.append(stripped)

        self._parser = _Inner()

    def feed(self, html: str) -> str:
        self._parser.feed(html)
        return "\n".join(self._parser.text_parts)


def web_fetch(url: str, prompt: str) -> str:
    import httpx

    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as e:
        raise ToolError(f"Failed to fetch {url}: {e}")

    content_type = resp.headers.get("content-type", "")
    if "html" in content_type:
        text = _TextExtractor().feed(resp.text)
    else:
        text = resp.text

    if len(text) > MAX_FETCH_CHARS:
        text = text[:MAX_FETCH_CHARS] + "\n... [truncated]"

    return f"[Fetched {url}]\n[Hint for relevance: {prompt}]\n\n{text}"


# ---------------------------------------------------------------------------
# todo_write
# ---------------------------------------------------------------------------

_CURRENT_TODOS: list[dict] = []

VALID_STATUSES = {"pending", "in_progress", "completed"}


def todo_write(todos: list[dict]) -> str:
    global _CURRENT_TODOS
    if not isinstance(todos, list):
        raise ToolError("todos must be a list of {content, status} objects")

    cleaned = []
    seen_in_progress = False
    for t in todos:
        content = str(t.get("content", "")).strip()
        if not content:
            continue
        status = t.get("status", "pending")
        if status not in VALID_STATUSES:
            status = "pending"
        if status == "in_progress":
            if seen_in_progress:
                status = "pending"  # demote extra in_progress items
            else:
                seen_in_progress = True
        cleaned.append({"content": content, "status": status})

    _CURRENT_TODOS = cleaned

    lines = []
    for t in cleaned:
        marker = {"completed": "[x]", "in_progress": "[~]", "pending": "[ ]"}[t["status"]]
        lines.append(f"{marker} {t['content']}")
    return "Todos updated:\n" + "\n".join(lines) if lines else "Todos cleared."


def get_current_todos() -> list[dict]:
    return list(_CURRENT_TODOS)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TOOL_REGISTRY = {
    "read_file":    lambda args: read_file(args["path"], args.get("offset"), args.get("limit")),
    "write_file":   lambda args: write_file(args["path"], args["content"]),
    "append_file":  lambda args: append_file(args["path"], args["content"]),
    "edit_file":    lambda args: edit_file(args["path"], args["old_string"], args["new_string"], args.get("replace_all", False)),
    "glob_files":   lambda args: glob_files(args["pattern"], args.get("path", ".")),
    "grep_search":  lambda args: grep_search(
        args["pattern"], args.get("path", "."), args.get("glob"), args.get("type"),
        args.get("output_mode", "files_with_matches"),
        args.get("context_before"), args.get("context_after"), args.get("context"),
        args.get("show_line_numbers", True), args.get("case_insensitive", False),
        args.get("head_limit"), args.get("offset", 0), args.get("multiline", False),
    ),
    "run_command":  lambda args: run_command(
        args["command"], args.get("description"), args.get("timeout"),
        args.get("run_in_background", False), args.get("cwd"),
    ),
    "bash_output":  lambda args: bash_output(args["bg_id"]),
    "kill_bash":    lambda args: kill_bash(args["bg_id"]),
    "list_dir":     lambda args: list_dir(args.get("path", ".")),
    "web_fetch":    lambda args: web_fetch(args["url"], args["prompt"]),
    "todo_write":   lambda args: todo_write(args["todos"]),
}

TOOL_DESCRIPTIONS = {
    "read_file":    "Read file contents (numbered lines)",
    "write_file":   "Create/overwrite a file (max 120k chars)",
    "append_file":  "Append content to an existing file (max 120k chars)",
    "edit_file":    "Replace exact text in a file",
    "glob_files":   "Find files by glob pattern",
    "grep_search":  "Search file contents (regex)",
    "run_command":  "Execute a shell command",
    "bash_output":  "Check output of a background command",
    "kill_bash":    "Kill a background command",
    "list_dir":     "List directory contents",
    "web_fetch":    "Fetch a URL and extract text",
    "todo_write":   "Update the session todo checklist",
}

# read-only tools: auto-allowed, no permission prompt
READ_TOOLS = {"read_file", "glob_files", "grep_search", "list_dir", "bash_output", "todo_write"}
WRITE_TOOLS = {"write_file", "append_file", "edit_file"}
EXEC_TOOLS = {"run_command", "kill_bash"}
NETWORK_TOOLS = {"web_fetch"}
