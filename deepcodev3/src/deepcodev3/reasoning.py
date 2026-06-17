from __future__ import annotations
import sys
from . import api, renderer

LEVELS = {
    "low":    {"stages": ["think"],                       "rounds": 0},
    "middle": {"stages": ["think", "critique"],            "rounds": 0},
    "high":   {"stages": ["think", "critique", "plan"],    "rounds": 1},
    "ultra":  {"stages": ["think", "critique", "plan", "refine"], "rounds": 2},
}

STAGE_PROMPTS = {
    "think": (
        "Think through the following task step by step. Identify key problems, "
        "constraints, and approaches. Output your reasoning only — do not answer yet.\n\nTask: {task}"
    ),
    "critique": (
        "Here is a task and your initial reasoning about it.\n\nTask: {task}\n\n"
        "Your reasoning:\n{think}\n\n"
        "Now critique your reasoning. What's wrong, missing, or could be improved? "
        "Be honest and specific."
    ),
    "plan": (
        "Here is a task, your reasoning, and your critique.\n\nTask: {task}\n\n"
        "Reasoning:\n{think}\n\nCritique:\n{critique}\n\n"
        "Now write a concrete step-by-step plan to solve the task correctly."
    ),
    "refine": (
        "Here is a task and your full reasoning chain so far.\n\nTask: {task}\n\n"
        "Reasoning:\n{think}\n\nCritique:\n{critique}\n\nPlan:\n{plan}\n\n"
        "Refine and improve the plan. Fix any remaining issues before execution."
    ),
}

STAGE_LABELS = {
    "think":   "Thinking",
    "critique": "Critique",
    "plan":    "Planning",
    "refine":  "Refining",
}


def _safe_write(s: str):
    """Write to stdout without ever raising on console encoding limits.
    A Windows cp1252 console can't encode ⏳/… — an UnicodeEncodeError here used
    to bubble up and kill the whole reasoning stage. Status output is cosmetic,
    so swallow any write failure."""
    try:
        sys.stdout.write(s)
        sys.stdout.flush()
    except Exception:
        pass


def _status(msg: str):
    _safe_write(f"  \033[2m⏳ {msg}\033[0m\r")


def _clear_status():
    _safe_write("\033[2K\r")


async def _run_stage(stage: str, task: str, context: dict, model_id: str,
                     memory_block: str, status_msg: str, quiet: bool = False) -> str:
    prompt = STAGE_PROMPTS[stage].format(task=task, **context)
    if memory_block:
        prompt = memory_block + "\n\n" + prompt
    text = ""
    dots = 0
    async for chunk in api.stream_chat(prompt, model_id):
        if chunk.get("delta"):
            text += chunk["delta"]
            dots += 1
            if not quiet and dots % 20 == 0:
                _status(f"{status_msg} {'.' * (dots // 20 % 4 + 1)}")
        if chunk.get("done"):
            break
    return text.strip()


async def run_reasoning(task: str, model_id: str, level: str, system_prompt: str,
                        memory_block: str = "", quiet: bool = False) -> str:
    """quiet=True suppresses all stdout (status spinner + reasoning panels) —
    required when called from a swarm worker, which must not write to the
    shared terminal / TUI (see ultracode-workflows-lessons)."""
    cfg = LEVELS.get(level, LEVELS["middle"])
    stages = cfg["stages"]
    extra_rounds = cfg["rounds"]
    context: dict[str, str] = {}

    _show_status = (lambda m: None) if quiet else _status
    _hide_status = (lambda: None) if quiet else _clear_status
    _show_block = (lambda lbl, txt: None) if quiet else _print_reasoning_block

    all_rounds = 1 + extra_rounds
    total_steps = (len(stages) * all_rounds) + 1  # +1 for final answer
    step = 0

    for rnd in range(all_rounds):
        round_label = f" (round {rnd+1}/{all_rounds})" if all_rounds > 1 else ""
        for stage in stages:
            step += 1
            msg = f"[{step}/{total_steps}] {STAGE_LABELS[stage]}{round_label}"
            _show_status(msg)
            try:
                result = await _run_stage(stage, task, context, model_id, memory_block, msg, quiet=quiet)
            except Exception as e:
                _hide_status()
                if not quiet:
                    renderer.print_error(str(e))
                return ""
            context[stage] = result
            _hide_status()
            _show_block(f"{STAGE_LABELS[stage]}{round_label}", result)

    # Final answer
    step += 1
    _show_status(f"[{step}/{total_steps}] Answering")
    chain = "\n\n".join(
        f"{STAGE_LABELS[s].upper()}:\n{context[s]}"
        for s in stages if s in context
    )
    base = f"{system_prompt}\n\n{memory_block}\n\n" if memory_block else f"{system_prompt}\n\n"
    final_prompt = (
        base +
        f"[Reasoning chain]\n{chain}\n\n"
        f"Now answer the task using your reasoning above. Be concise and direct.\n\n"
        f"User: {task}\nAssistant:"
    )

    full_answer = ""
    try:
        async for chunk in api.stream_chat(final_prompt, model_id):
            if chunk.get("delta"):
                full_answer += chunk["delta"]
            if chunk.get("done"):
                break
    except Exception as e:
        if not quiet:
            renderer.print_error(str(e))

    _hide_status()
    return full_answer.strip()


def _print_reasoning_block(label: str, text: str):
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.padding import Padding

    c = Console(highlight=False)
    body = Text(text, style="dim")
    c.print(Padding(
        Panel(body, title=f"[dim]{label}[/dim]", border_style="dim cyan", padding=(0, 1)),
        pad=(0, 0, 0, 2),
    ))
