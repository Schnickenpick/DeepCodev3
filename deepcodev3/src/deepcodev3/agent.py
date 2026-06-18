from __future__ import annotations
import json
import re
import sys
from . import api, renderer
from .tool_schema import TOOL_REGISTRY, TOOL_DESCRIPTIONS
from .tools import ToolError
from .permissions import ask_permission, OPTION_DENY
from .system_prompt import SYSTEM_PROMPT

TOOL_TAG_RE = re.compile(r'<tool>\s*(\{.*?\})\s*</tool>', re.DOTALL)
QUIZ_RE = re.compile(r'<quiz>([\s\S]*?)</quiz>')

# The underlying gateway sometimes leaks Claude harness-internal
# <system-reminder> blocks into raw completions. Strip them — they are not
# part of our own context-injection system and confuse the model.
SYSTEM_REMINDER_RE = re.compile(r'<system[-_]reminder>[\s\S]*?</system[-_]reminder>', re.IGNORECASE)


def _strip_leaked_reminders(text: str) -> str:
    return SYSTEM_REMINDER_RE.sub("", text)


# Our flat-text prompt format uses literal "Tool result: [name]\n<output>",
# "User: ...", "Assistant:" turn markers (_build_prompt). On long contexts
# weaker models start hallucinating/echoing fake turns in this format after
# their real tool calls. Cut the response at the first such fake marker so
# the hallucinated turns never enter conversation history or get shown.
FAKE_TURN_RE = re.compile(r'\n\s*(?:Tool result:\s*\[|User:\s|Assistant:\s*$|A:\s*$)', re.MULTILINE)


def _strip_fake_turns(text: str) -> str:
    m = FAKE_TURN_RE.search(text)
    if m:
        return text[:m.start()].rstrip()
    return text

MAX_ITERATIONS = 20

# Context window budget: ~133k tokens (100k words). Compress at 75%.
CONTEXT_TOKEN_LIMIT = 133_000
COMPRESS_THRESHOLD = 0.75
TAIL_PROTECTED = 6  # always keep last N messages uncompressed

# Tool result/assistant msg char caps (pre-compression safety)
_MAX_TOOL_RESULT_CHARS = 8000
_MAX_ASSISTANT_CHARS = 12000


_tiktoken_enc = None

def _get_enc():
    global _tiktoken_enc
    if _tiktoken_enc is None:
        try:
            import tiktoken
            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            pass
    return _tiktoken_enc

def _count_tokens(text: str) -> int:
    enc = _get_enc()
    if enc:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return len(text) // 4


def _build_prompt(conversation: list[dict], memory_md: str = "", user_md: str = "", deepcode_md: str = "", project_memory_md: str = "") -> str:
    parts = [SYSTEM_PROMPT, "\n\n"]
    if deepcode_md:
        parts.append(f"[Project context from DEEPCODE.md:\n{deepcode_md}\n]\n\n")
    if user_md:
        parts.append(f"[User profile:\n{user_md}\n]\n\n")
    if memory_md:
        parts.append(f"[Memory:\n{memory_md}\n]\n\n")
    if project_memory_md:
        parts.append(f"[Project memory:\n{project_memory_md}\n]\n\n")
    for msg in conversation:
        role = msg["role"]
        content = msg["content"]
        if role == "user":
            parts.append(f"User: {content}\n\n")
        elif role == "assistant":
            content = _strip_leaked_reminders(content)
            if len(content) > _MAX_ASSISTANT_CHARS:
                content = content[:_MAX_ASSISTANT_CHARS] + "\n... [truncated]"
            parts.append(f"Assistant: {content}\n\n")
        elif role == "tool_result":
            content = _strip_leaked_reminders(content)
            if len(content) > _MAX_TOOL_RESULT_CHARS:
                content = content[:_MAX_TOOL_RESULT_CHARS] + "\n... [truncated]"
            parts.append(f"Tool result: {content}\n\n")
    parts.append("Assistant:")
    return "".join(parts)


async def _compress_conversation(conversation: list[dict], model_id: str, prev_summary: str = "") -> tuple[list[dict], str]:
    """Multi-phase compressor. Returns (compressed_conversation, new_summary)."""
    if len(conversation) <= TAIL_PROTECTED * 2:
        return conversation, prev_summary

    tail = conversation[-TAIL_PROTECTED:]
    middle = conversation[:-TAIL_PROTECTED]

    # Phase 1: prune tool results in middle (free, no LLM)
    pruned = []
    for msg in middle:
        if msg["role"] == "tool_result" and len(msg["content"]) > 500:
            pruned.append({"role": "tool_result", "content": "[tool output pruned for context]"})
        else:
            pruned.append(msg)

    # Phase 2: LLM summarize the middle
    history_text = ""
    for msg in pruned:
        role = msg["role"]
        content = msg["content"]
        history_text += f"{role.upper()}: {content}\n\n"

    update_instruction = (
        f"Previous summary:\n{prev_summary}\n\nUpdate it with the new conversation below." if prev_summary
        else "Summarize the conversation below."
    )
    summary_prompt = (
        f"{update_instruction}\n\n"
        f"Conversation:\n{history_text}\n\n"
        "Write a structured summary with these sections:\n"
        "GOALS: what the user is trying to accomplish\n"
        "DECISIONS: key choices made\n"
        "PROGRESS: what has been done/built so far\n"
        "NEXT: what was planned next\n\n"
        "Be dense and specific. Under 400 words."
    )

    summary = ""
    try:
        async for chunk in api.stream_chat(summary_prompt, model_id):
            if chunk.get("delta"):
                summary += chunk["delta"]
            if chunk.get("done"):
                break
        summary = summary.strip()
    except Exception:
        summary = prev_summary or "[summary unavailable]"

    # Phase 3: assemble — summary node + protected tail
    compressed = [{"role": "user", "content": f"[Conversation summary]\n{summary}"}] + tail
    return compressed, summary


def _extract_json_objects(text: str) -> list[tuple[str, dict]]:
    """Find all top-level JSON objects in text using brace counting. Returns (match_str, parsed) pairs."""
    results = []
    i = 0
    while i < len(text):
        if text[i] != '{':
            i += 1
            continue
        depth = 0
        in_string = False
        escape = False
        start = i
        for j in range(i, len(text)):
            ch = text[j]
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:j+1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict) and "name" in obj and "args" in obj:
                            results.append((candidate, obj))
                    except Exception:
                        pass
                    i = j + 1
                    break
        else:
            break
    return results


def _parse_tool_calls(text: str) -> list[dict]:
    calls = []
    # Try tagged format first
    for m in TOOL_TAG_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
            if "name" in obj and "args" in obj:
                calls.append({"match": m.group(0), "name": obj["name"], "args": obj["args"]})
        except Exception:
            pass
    if not calls:
        # Fallback: brace-counting extractor handles arbitrarily nested/long JSON
        for match_str, obj in _extract_json_objects(text):
            calls.append({"match": match_str, "name": obj["name"], "args": obj["args"]})

    # Tolerate models that emit an "ask_user_question" tool call instead of a
    # <quiz> block — rewrite it into the quiz format so the existing quiz
    # loop in chat.py handles it.
    rewritten = []
    for c in calls:
        if c["name"] == "ask_user_question":
            args = c.get("args") or {}
            question = args.get("question", "")
            options = args.get("options", [])
            quiz_json = json.dumps({"question": question, "options": options})
            rewritten.append({"match": c["match"], "name": "__quiz_rewrite__", "quiz_tag": f"<quiz>{quiz_json}</quiz>"})
        else:
            rewritten.append(c)
    return rewritten


def _strip_tool_calls(text: str, calls: list[dict]) -> str:
    for c in calls:
        if c["name"] == "__quiz_rewrite__":
            text = text.replace(c["match"], c["quiz_tag"])
        else:
            text = text.replace(c["match"], "")
    return text.strip()


def _format_call_args(tool_name: str, args: dict) -> str:
    if tool_name in ("read_file", "write_file", "edit_file", "append_file", "list_dir", "glob_files"):
        return args.get("path", args.get("pattern", ""))
    if tool_name in ("run_command", "kill_bash"):
        return str(args.get("command", args.get("bg_id", "")))[:100]
    if tool_name == "grep_search":
        return f"{args.get('pattern','')!r} in {args.get('path','.')}"
    if tool_name == "web_fetch":
        return args.get("url", "")
    if tool_name == "bash_output":
        return str(args.get("bg_id", ""))
    return ""


def _show_tool_call(tool_name: str, args: dict):
    """Print the claude-code-style '⏺ ToolName(args)' call line before execution."""
    from . import renderer
    arg_str = _format_call_args(tool_name, args)
    suffix = f"[dim]({arg_str})[/dim]" if arg_str else ""
    renderer.console.print(f"  [{renderer.CLAUDE_ORANGE} bold]{renderer.BLACK_CIRCLE}[/{renderer.CLAUDE_ORANGE} bold] [bold]{tool_name}[/bold]{suffix}")


def _show_diff(old_string: str, new_string: str):
    from . import renderer
    c = renderer.console
    old_lines = old_string.splitlines()
    new_lines = new_string.splitlines()
    for line in old_lines:
        c.print(f"       [{renderer.ERROR_RED}]- {line[:118]}[/{renderer.ERROR_RED}]")
    for line in new_lines:
        c.print(f"       [{renderer.SUCCESS_GREEN}]+ {line[:118]}[/{renderer.SUCCESS_GREEN}]")


def _show_tool_result(tool_name: str, result: str, success: bool, args: dict | None = None):
    from . import renderer
    c = renderer.console
    lines = result.splitlines()
    color = renderer.SUCCESS_GREEN if success else renderer.ERROR_RED

    if not lines:
        c.print(f"    [dim]⎿[/dim]  [{color}]{'done' if success else 'error'}[/{color}]")
    else:
        c.print(f"    [dim]⎿[/dim]  [dim]{lines[0][:120]}[/dim]")
        for line in lines[1:5]:
            c.print(f"       [dim]{line[:120]}[/dim]")
        if len(lines) > 5:
            c.print(f"       [dim]… +{len(lines)-5} more lines[/dim]")

    if tool_name == "edit_file" and success and args:
        _show_diff(args.get("old_string", ""), args.get("new_string", ""))

    c.print()

    if tool_name == "todo_write" and success:
        from .tools import get_current_todos
        renderer.print_todos(get_current_todos())


async def run_agent(user_message: str, conversation: list[dict], memory_md: str, user_md: str, model_id: str, deepcode_md: str = "", project_memory_md: str = "", session=None, _summary: str = "", swarm_mode: bool = False, on_event=None):
    """on_event(dict), if given, is called with live progress events:
    {"type": "thinking"} | {"type": "tool_call", "name": str, "args": dict}
    | {"type": "tool_result", "name": str, "ok": bool}
    | {"type": "tokens", "count": int} | {"type": "response", "text": str}
    Used by UltraCode to drive live swarm status displays.
    """
    conversation.append({"role": "user", "content": user_message})

    for iteration in range(MAX_ITERATIONS):
        prompt = _build_prompt(conversation, memory_md, user_md, deepcode_md, project_memory_md)

        # Compress if over threshold — cheap char estimate first, exact count only if close
        _char_estimate = len(prompt) // 4
        if _char_estimate > CONTEXT_TOKEN_LIMIT * COMPRESS_THRESHOLD * 0.8:
            if _count_tokens(prompt) > CONTEXT_TOKEN_LIMIT * COMPRESS_THRESHOLD:
                if not swarm_mode:
                    renderer.print_info("Compressing context...")
                conversation, _summary = await _compress_conversation(conversation, model_id, _summary)
                prompt = _build_prompt(conversation, memory_md, user_md, deepcode_md, project_memory_md)
        full_response = ""
        _show_thinking_dot = True

        async def _stream_with_indicator():
            nonlocal full_response, _show_thinking_dot
            import asyncio
            dot_task = asyncio.get_event_loop().create_task(_thinking_dots())
            try:
                async for chunk in api.stream_chat(prompt, model_id):
                    if chunk.get("error"):
                        if swarm_mode:
                            if on_event:
                                on_event({"type": "response", "text": f"[error] {chunk['error']}"})
                        else:
                            renderer.print_error(chunk["error"])
                        break
                    if chunk.get("done"):
                        break
                    delta = chunk.get("delta", "")
                    if delta:
                        full_response += delta
                        # live token streaming for non-console frontends (GUI
                        # bridge). Harmless for the TUI, which ignores on_event.
                        if on_event:
                            on_event({"type": "delta", "text": delta})
            finally:
                _show_thinking_dot = False
                dot_task.cancel()
                try:
                    await asyncio.shield(dot_task)
                except Exception:
                    pass
                if not renderer.is_tui_active() and not swarm_mode and not renderer.is_bottom_bar_active():
                    sys.stdout.write("\033[2K\r")
                    sys.stdout.flush()

        async def _thinking_dots():
            import asyncio
            frames = ["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"]
            i = 0
            LIGHT_BLUE = "\033[38;2;137;180;250m"
            RESET = "\033[0m"
            try:
                while _show_thinking_dot:
                    # Suppressed when the bottom bar owns the screen — the bar's
                    # mode line shows live tokens/elapsed instead of a \r spinner.
                    if not renderer.is_tui_active() and not swarm_mode and not renderer.is_bottom_bar_active():
                        sys.stdout.write(f"\r  {LIGHT_BLUE}{frames[i % len(frames)]} Thinking…{RESET}")
                        sys.stdout.flush()
                    i += 1
                    await asyncio.sleep(0.08)
            except asyncio.CancelledError:
                pass

        if on_event:
            on_event({"type": "thinking"})

        try:
            await _stream_with_indicator()
        except Exception as e:
            if swarm_mode:
                if on_event:
                    on_event({"type": "response", "text": f"[error] {e}"})
            else:
                renderer.print_error(str(e))
            break

        full_response = _strip_leaked_reminders(full_response)
        full_response = _strip_fake_turns(full_response)

        if on_event:
            on_event({"type": "tokens", "count": _count_tokens(full_response)})

        tool_calls = _parse_tool_calls(full_response)

        # Detect a truncated/unclosed <tool> call — model hit its output limit
        # mid-JSON (e.g. a huge write_file). Don't show the raw JSON to the
        # user; tell the model to retry in smaller chunks instead.
        #
        # Guard against FALSE positives: prose can mention "<tool>" literally
        # (e.g. a summary describing the tool format, or text inside backticks).
        # A real truncated call is an unclosed <tool> immediately followed by a
        # JSON object open `{` AND not wrapped in an inline-code backtick span.
        if not tool_calls and "<tool>" in full_response:
            last_open = full_response.rfind("<tool>")
            after = full_response[last_open + len("<tool>"):]
            looks_like_call = re.match(r'\s*\{', after) is not None
            # mentioned inside an inline code span like `...<tool>...` → prose
            in_backticks = full_response[:last_open].count("`") % 2 == 1
            if (looks_like_call and not in_backticks
                    and "</tool>" not in full_response[last_open:]):
                truncated_text = full_response[:last_open].strip()
                truncated_text = QUIZ_RE.sub("", truncated_text).strip()
                if truncated_text and not swarm_mode:
                    renderer.print_assistant_header(model_id)
                    renderer.finish_stream(truncated_text)
                conversation.append({"role": "assistant", "content": full_response})
                conversation.append({
                    "role": "tool_result",
                    "content": (
                        "Error: your <tool> call was cut off because the response was too long. "
                        "Retry with a much smaller chunk: use write_file for a short first chunk "
                        "(e.g. just the HTML skeleton/head), then append_file repeatedly for the rest. "
                        "Each tool call's content must be small enough to complete in one response."
                    ),
                })
                if not swarm_mode:
                    renderer.print_error("Response truncated mid tool-call — asking model to retry in smaller chunks.")
                continue

        visible = _strip_tool_calls(full_response, tool_calls).strip()

        # Strip quiz block before printing — caller handles quiz display
        visible_clean = QUIZ_RE.sub("", visible).strip()

        if visible_clean:
            if on_event:
                on_event({"type": "response", "text": visible_clean})
            if not swarm_mode:
                renderer.print_assistant_header(model_id)
                renderer.finish_stream(visible_clean)

        # Quiz rewrites (from ask_user_question) leave a <quiz> tag inside `visible` —
        # treat that as "no real tool call", let the caller's quiz loop handle it.
        real_calls = [c for c in tool_calls if c["name"] != "__quiz_rewrite__"]

        if not real_calls:
            conversation.append({"role": "assistant", "content": visible or full_response})
            return visible, conversation

        conversation.append({"role": "assistant", "content": full_response})

        all_denied = True
        for call in real_calls:
            name = call["name"]
            args = call["args"]

            if name not in TOOL_REGISTRY:
                err = f"Unknown tool: {name}"
                if not swarm_mode:
                    renderer.print_error(err)
                conversation.append({"role": "tool_result", "content": err})
                continue

            if not swarm_mode:
                decision = ask_permission(name, args)
                if decision == OPTION_DENY:
                    conversation.append({"role": "tool_result", "content": "User denied."})
                    continue

            all_denied = False
            if not swarm_mode:
                _show_tool_call(name, args)
            if on_event:
                on_event({"type": "tool_call", "name": name, "args": args})
            try:
                result = TOOL_REGISTRY[name](args)
                result = _strip_leaked_reminders(result)
                if not swarm_mode:
                    _show_tool_result(name, result, True, args)
                if on_event:
                    on_event({"type": "tool_result", "name": name, "ok": True})
            except Exception as e:
                result = f"Error: {e}"
                if not swarm_mode:
                    _show_tool_result(name, result, False, args)
                if on_event:
                    on_event({"type": "tool_result", "name": name, "ok": False})
            conversation.append({"role": "tool_result", "content": f"[{name}]\n{result}"})

        if all_denied:
            if not swarm_mode:
                renderer.print_info("All tools denied.")
            break

    return "", conversation
