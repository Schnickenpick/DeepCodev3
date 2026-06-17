"""
UltraCode: Recursive hierarchical agent swarm with DAG dependency scheduling.

Hierarchy scales automatically:
  1-20 agents:   Leader → Workers (flat)
  21-100:        Leader → Managers → Workers
  101-500:       Leader → Managers → Sub-Managers → Workers
  501-1000:      Leader → Managers → Sub-Managers → Sub-Sub-Managers → Workers

Each "group" = one manager + N workers. Groups form a DAG — dependencies are
resolved before a group starts, and its artifacts are injected into dependents.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from . import api, renderer
from .agent import run_agent, _format_call_args
from .system_prompt import SYSTEM_PROMPT

# ── constants ────────────────────────────────────────────────────────────────

MAX_CHILDREN = 10          # max direct reports per node
MAX_AGENTS   = 1000
DEFAULT_AGENTS = 50
_CONTEXT_BUDGET = 8000     # chars of artifact context injected per group

# ── status enum ──────────────────────────────────────────────────────────────

class Status(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"
    BLOCKED  = "blocked"   # deps not yet done


# ── data structures ───────────────────────────────────────────────────────────

@dataclass
class Artifact:
    group_id: str
    name: str       # short slug, e.g. "db_schema"
    content: str    # textual output / summary
    files: list[str] = field(default_factory=list)  # files written to disk


@dataclass
class GroupPlan:
    id: str
    role: str            # human-readable role description
    goal: str            # specific goal for this group
    depends_on: list[str] = field(default_factory=list)
    size: int = 3        # number of workers (not counting manager)
    depth: int = 0       # 0 = leaf group, >0 = spawns sub-groups recursively
    artifact_name: str = ""   # slug for output artifact
    allowed_files: list[str] = field(default_factory=list)  # files workers may write — empty = unconstrained
    reasoning: str = "off"    # per-group thinking budget: off/low/middle/high/ultra (leader-assigned)


@dataclass
class GroupResult:
    group_id: str
    artifact: Artifact
    status: Status
    elapsed: float = 0.0


# ── live state (for TUI) ───────────────────────────────────────────────────────

@dataclass
class AgentState:
    """Live status of one worker agent, updated as it runs."""
    agent_id: str          # e.g. "G1:W2"
    group_id: str
    name: str               # short task description shown as "agent name"
    model_id: str
    status: Status = Status.PENDING
    action: str = ""        # current activity, e.g. "Thinking…" / "write_file(out.py)"
    reasoning: str = "off"  # thinking budget assigned to this agent's group
    tokens: int = 0
    tool_calls: int = 0
    started_at: float = 0.0
    elapsed: float = 0.0
    log: list[str] = field(default_factory=list)  # human-readable activity log ("agent chat")


@dataclass
class SwarmState:
    """Shared live state for the whole swarm — read by the TUI."""
    id: str = ""
    task: str = ""
    plans: list[GroupPlan] = field(default_factory=list)
    agents: dict = field(default_factory=dict)  # agent_id -> AgentState
    group_status: dict = field(default_factory=dict)  # group_id -> Status
    started_at: float = field(default_factory=time.time)
    finished: bool = False
    failed: bool = False
    error: str = ""
    extra_notes: list[str] = field(default_factory=list)  # live user instructions, injected into pending groups

    def agents_for_group(self, group_id: str) -> list["AgentState"]:
        return [a for a in self.agents.values() if a.group_id == group_id]


# Module-level registry of swarms (running + recently finished) for /workflows.
ACTIVE_SWARMS: list["SwarmState"] = []


# ── context gatherer ──────────────────────────────────────────────────────────

def gather_project_context(max_chars: int = 12000) -> str:
    """
    Scan cwd for project context. Prioritizes .md files, then source files.
    Returns a string summary injected into the leader prompt.
    """
    cwd = Path.cwd()
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv",
                 "dist", "build", ".egg-info", "site-packages"}

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(cwd))
        except ValueError:
            return str(p)

    # collect files — .md first, then source, then config
    md_files: list[Path] = []
    src_files: list[Path] = []
    cfg_files: list[Path] = []

    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        rp = Path(root)
        depth = len(rp.relative_to(cwd).parts)
        if depth > 4:
            dirs.clear()
            continue
        for f in files:
            fp = rp / f
            ext = fp.suffix.lower()
            if ext == ".md":
                md_files.append(fp)
            elif ext in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
                         ".java", ".cs", ".cpp", ".c", ".h"}:
                src_files.append(fp)
            elif f in {"pyproject.toml", "package.json", "Cargo.toml",
                       "go.mod", "requirements.txt", "tsconfig.json",
                       "Makefile", "Dockerfile", ".env.example"}:
                cfg_files.append(fp)

    parts: list[str] = []
    used = 0

    def _add(fp: Path, max_file_chars: int = 2000):
        nonlocal used
        if used >= max_chars:
            return
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
            snippet = content[:max_file_chars]
            chunk = f"\n--- {_rel(fp)} ---\n{snippet}"
            parts.append(chunk)
            used += len(chunk)
        except Exception:
            pass

    # .md files get generous budget (most info-dense)
    for fp in sorted(md_files, key=lambda p: (len(p.parts), p.name)):
        _add(fp, 3000)

    # config files
    for fp in cfg_files:
        _add(fp, 1000)

    # source files — just small snippets for structure awareness
    for fp in sorted(src_files, key=lambda p: len(p.parts))[:30]:
        _add(fp, 400)

    return "".join(parts)[:max_chars]


# ── leader: produces a flat DAG plan ─────────────────────────────────────────

LEADER_PROMPT = """\
You are the LEADER of an UltraCode agent swarm. Your job is to decompose a large task \
into a flat list of groups that form a DAG (directed acyclic graph). Each group is a \
"manager + team" unit that works on one domain of the task.

RULES:
- YOU decide how many total agents this task needs (1-{max_agents}). Small/focused tasks \
might need just 1-5 agents total; large builds might need 50-200+. Don't over-provision — \
pick the smallest swarm that can do the job well. Set "total_agents" in your output to your choice.
- Each group has at most {max_children} workers
- If a group is too large (>100 workers needed), set depth=1 so it spawns sub-groups recursively
- depend_on lists the group IDs that must finish before this group starts
- artifact_name is a short slug (snake_case) for the output this group produces
- "goal" MUST name the exact output file(s) the group will write to disk (e.g. "Write game.html", "Write src/engine.js")
- Groups that can run in parallel should have no shared deps
- Order deps correctly: if group B needs group A's output, B must list A in depends_on
- Scale group count to match the task: big task = more groups, small = fewer
- Assign each group a clear role and specific goal

FILE DISCIPLINE (CRITICAL — obey the user's file layout exactly):
- Read the MASTER GOAL for an explicit file constraint: "in 1 html file", "single file", "one file", \
"everything in X.html", a named file, or a specific count. That constraint is HARD. Never exceed it.
- If the user asked for a SINGLE file (e.g. "make a game in 1 html file"): output EXACTLY ONE group with \
size=1. That one worker writes the entire program into that one file. Do NOT split CSS/JS/HTML into \
separate files. Do NOT invent helper files (.js, .css, configs). Inline everything into the one file.
- Only split work across multiple groups/files when the user did NOT constrain file count AND the task \
is genuinely large enough to need separate files. When unsure, prefer FEWER files.
- Every file you name in any "goal" must be a file the user actually wants. Do not add scaffolding, \
build files, test files, or extra modules unless the task explicitly calls for them.
- Set "allowed_files" to the exhaustive list of file paths this group is permitted to write. \
Workers in this group may write ONLY these files — nothing else. If the user named one file, \
allowed_files is that one file for the one group.

REASONING (you assign each group a thinking budget):
- Set "reasoning" per group: one of "off", "low", "middle", "high", "ultra".
- off  = trivial/mechanical work (boilerplate, single obvious file).
- low/middle = normal implementation with some design choices.
- high/ultra = hard algorithmic/architectural work, tricky correctness, or the core/hardest group.
- Spend the budget where it matters. Most groups are off/low. Reserve high/ultra for the genuinely hard one.

PROJECT CONTEXT:
{context}

MASTER GOAL: {task}

Output ONLY valid JSON, no markdown fences:
{{
  "total_agents": <number>,
  "groups": [
    {{
      "id": "G1",
      "role": "short role name",
      "goal": "specific goal for this group",
      "depends_on": [],
      "size": <workers 2-{max_children}>,
      "depth": 0,
      "artifact_name": "slug",
      "allowed_files": ["exact/path/one.html"],
      "reasoning": "off"
    }},
    ...
  ]
}}
"""


async def _call_llm(prompt: str, model_id: str) -> str:
    """Stream a complete response from the LLM."""
    result = ""
    async for chunk in api.stream_chat(prompt, model_id):
        if chunk.get("delta"):
            result += chunk["delta"]
        if chunk.get("done"):
            break
    return result.strip()


async def run_leader(task: str, model_id: str,
                     context: str, memory_md: str, user_md: str,
                     deepcode_md: str) -> tuple[list[GroupPlan], int]:
    """Ask leader LLM to produce a DAG plan. Returns (plans, total_agents chosen by leader)."""
    ctx_block = ""
    if deepcode_md:
        ctx_block += f"\n[DEEPCODE.md]\n{deepcode_md}\n"
    if memory_md:
        ctx_block += f"\n[Memory]\n{memory_md}\n"
    if user_md:
        ctx_block += f"\n[User]\n{user_md}\n"
    ctx_block += context

    prompt = LEADER_PROMPT.format(
        max_agents=MAX_AGENTS,
        max_children=MAX_CHILDREN,
        context=ctx_block[:10000],
        task=task,
    )

    raw = await _call_llm(prompt, model_id)

    # strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # fallback: extract first JSON object
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(raw[start:end])
        else:
            raise ValueError(f"Leader returned unparseable plan:\n{raw[:500]}")

    plans = []
    for g in data.get("groups", []):
        reasoning = str(g.get("reasoning", "off")).lower()
        if reasoning not in {"off", "low", "middle", "high", "ultra"}:
            reasoning = "off"
        af = g.get("allowed_files", []) or []
        if isinstance(af, str):
            af = [af]
        plans.append(GroupPlan(
            id=g["id"],
            role=g.get("role", g["id"]),
            goal=g.get("goal", ""),
            depends_on=g.get("depends_on", []),
            size=min(int(g.get("size", 3)), MAX_CHILDREN),
            depth=int(g.get("depth", 0)),
            artifact_name=g.get("artifact_name", g["id"].lower()),
            allowed_files=[str(f).strip() for f in af if str(f).strip()],
            reasoning=reasoning,
        ))

    total_agents = int(data.get("total_agents", sum(p.size for p in plans) or 1))
    total_agents = max(1, min(total_agents, MAX_AGENTS))
    return plans, total_agents


# ── manager prompt ────────────────────────────────────────────────────────────

MANAGER_PROMPT = """\
You are a MANAGER agent in an UltraCode swarm.

YOUR ROLE: {role}
YOUR GOAL: {goal}
MASTER TASK: {master_task}

DEPENDENCY ARTIFACTS (outputs from groups you depend on):
{dep_artifacts}

PROJECT CONTEXT (summary):
{context}

ALLOWED FILES (you and your workers may write ONLY these files):
{allowed_files}

You manage {n_workers} worker agents. Decompose your goal into exactly {n_workers} \
specific, atomic subtasks — one per worker. Each subtask must be independently \
executable and together they must fully achieve your goal.

CRITICAL: Each task string MUST include the exact filename(s) the worker should write to. \
Workers write files to disk — if the task doesn't name a file, the worker won't know where to write. \
Example good task: "Write the enemy AI class to enemies.js with patrol and attack behaviors" \
Example bad task: "Implement enemy AI" (no filename = worker writes nothing)

FILE DISCIPLINE (HARD CONSTRAINT):
- The target filename in every subtask MUST be one of the ALLOWED FILES above. Never invent new files.
- If ALLOWED FILES contains exactly ONE file and {n_workers} is 1: that single worker writes the WHOLE \
program into that one file — all HTML, CSS, and JS inlined together. Do not split into multiple files.
- If multiple workers must contribute to the SAME single file, you cannot parallelize that safely — \
instead emit ONE subtask that builds the entire file, and leave the other workers' tasks empty/trivial \
or fold their work into the one subtask. Prefer one complete file over a half-written one.
- Do NOT create config files, build files, or helper modules unless they appear in ALLOWED FILES.

Output ONLY valid JSON:
{{
  "subtasks": [
    {{"id": "W1", "task": "specific task naming a target file from ALLOWED FILES", "artifact_name": "slug"}},
    ...
  ]
}}
"""


async def run_manager(plan: GroupPlan, master_task: str, dep_artifacts: list[Artifact],
                      model_id: str, context: str) -> list[dict]:
    """Manager LLM decomposes goal into worker subtasks."""
    dep_text = ""
    for a in dep_artifacts:
        snippet = a.content[:1500]
        dep_text += f"\n[{a.name} from {a.group_id}]\n{snippet}\n"
    if not dep_text:
        dep_text = "(none)"

    allowed = "\n".join(f"- {f}" for f in plan.allowed_files) if plan.allowed_files else \
        "(no explicit list — infer minimal files from the goal; do NOT sprawl across many files)"

    prompt = MANAGER_PROMPT.format(
        role=plan.role,
        goal=plan.goal,
        master_task=master_task,
        dep_artifacts=dep_text,
        context=context[:3000],
        n_workers=plan.size,
        allowed_files=allowed,
    )

    raw = await _call_llm(prompt, model_id)
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(raw)
        return data.get("subtasks", [])
    except Exception:
        # fallback: one subtask = the whole goal
        return [{"id": "W1", "task": plan.goal, "artifact_name": plan.artifact_name}]


# ── worker: runs run_agent for one subtask ────────────────────────────────────

WORKER_SYSTEM = """\
You are a WORKER agent in an UltraCode swarm.
Master task: {master_task}
Your group role: {role}
Your specific subtask: {subtask}

Dependency context:
{dep_artifacts}
{plan_block}
FILE CONSTRAINT:
{allowed_files}

CRITICAL RULES:
- You MUST call write_file or append_file to produce output. Text responses without tool calls do nothing.
- Write ONLY to the file(s) listed under FILE CONSTRAINT. Do NOT create any other file — no extra .js/.css/config/helper files. If the constraint names one file, put EVERYTHING in that one file (inline all HTML/CSS/JS).
- Write complete, working implementations — no placeholders, no TODOs, no "add your code here".
- For large files (>500 lines): write_file for first chunk, then append_file for rest.
- Do not ask questions. Do not describe what you will do. Just call the tools and do it.
- When all files are written, output a one-line summary of files created.
"""


async def run_worker(
    subtask: dict,
    group_plan: GroupPlan,
    master_task: str,
    dep_artifacts: list[Artifact],
    model_id: str,
    memory_md: str,
    user_md: str,
    deepcode_md: str,
    project_memory_md: str,
    swarm_state: Optional["SwarmState"] = None,
    agent_id: str = "",
) -> Artifact:
    agent_state: Optional[AgentState] = None
    if swarm_state is not None and agent_id:
        agent_state = AgentState(
            agent_id=agent_id,
            group_id=group_plan.id,
            name=subtask.get("task", "")[:60],
            model_id=model_id,
            reasoning=getattr(group_plan, "reasoning", "off"),
            status=Status.RUNNING,
            started_at=time.time(),
        )
        swarm_state.agents[agent_id] = agent_state

    def _on_event(ev: dict):
        if not agent_state:
            return
        agent_state.elapsed = time.time() - agent_state.started_at
        etype = ev["type"]
        if etype == "thinking":
            agent_state.action = "Thinking…"
        elif etype == "tokens":
            agent_state.tokens += ev["count"]
        elif etype == "tool_call":
            agent_state.tool_calls += 1
            arg_str = _format_call_args(ev["name"], ev.get("args", {}))
            agent_state.action = f"{ev['name']}({arg_str})" if arg_str else ev["name"]
            agent_state.log.append(f"● {agent_state.action}")
        elif etype == "tool_result":
            status_word = "ok" if ev["ok"] else "error"
            agent_state.log.append(f"  ⎿ {status_word}")
        elif etype == "response":
            agent_state.log.append(ev["text"])

    dep_text = ""
    for a in dep_artifacts:
        dep_text += f"\n[{a.name}]\n{a.content[:_CONTEXT_BUDGET // max(len(dep_artifacts), 1)]}\n"

    # Per-group reasoning: master assigned a thinking budget to this group.
    # If > off, the worker first reasons about the subtask, then executes with that plan in hand.
    plan_block = ""
    level = getattr(group_plan, "reasoning", "off")
    if level and level != "off":
        if agent_state:
            agent_state.action = f"Reasoning ({level})…"
        try:
            from .reasoning import run_reasoning
            reasoning_task = (
                f"Subtask: {subtask['task']}\n"
                f"Group role: {group_plan.role}\n"
                f"Master task: {master_task}"
            )
            plan_text = await run_reasoning(
                reasoning_task, model_id, level, SYSTEM_PROMPT, memory_block="", quiet=True
            )
            if plan_text:
                plan_block = (
                    "\nYOUR PLAN (you already reasoned this through — follow it, "
                    "don't re-derive):\n" + plan_text.strip() + "\n"
                )
        except Exception:
            plan_block = ""  # reasoning is best-effort; never block execution

    # constraint shown to the worker — its own subtask file plus the group's allowed list
    allowed = group_plan.allowed_files
    if allowed:
        allowed_block = "Write ONLY these file(s): " + ", ".join(allowed)
    else:
        allowed_block = (
            "No explicit file list. Write the minimal set of files the subtask names — "
            "do NOT invent extra files. Prefer a single file when the task allows it."
        )

    # inject swarm context into deepcode_md so run_agent sees it
    swarm_ctx = WORKER_SYSTEM.format(
        master_task=master_task,
        role=group_plan.role,
        subtask=subtask["task"],
        dep_artifacts=dep_text or "(none)",
        plan_block=plan_block,
        allowed_files=allowed_block,
    )
    combined_deepcode = f"{swarm_ctx}\n\n{deepcode_md}".strip()

    # Force the worker message to explicitly demand file output
    worker_message = (
        f"{subtask['task']}\n\n"
        "IMPORTANT: You MUST write your output to disk using write_file or append_file. "
        "Do not just describe what you would write — actually call write_file with the real content. "
        "Complete the entire implementation and write all necessary files before responding."
    )

    conversation: list[dict] = []
    try:
        result_text, conversation = await run_agent(
            worker_message,
            conversation,
            memory_md,
            user_md,
            model_id,
            combined_deepcode,
            project_memory_md,
            _summary="",
            swarm_mode=True,
            on_event=_on_event,
        )
        if agent_state:
            agent_state.status = Status.DONE
            agent_state.action = "Done"
    except Exception:
        if agent_state:
            agent_state.status = Status.FAILED
            agent_state.action = "Failed"
        raise
    finally:
        if agent_state:
            agent_state.elapsed = time.time() - agent_state.started_at

    import re as _re

    # strip raw <tool>...</tool> calls from result_text — artifact content must be clean prose
    result_clean = _re.sub(r'<tool>\s*\{.*?\}\s*</tool>', '', result_text or "", flags=_re.DOTALL).strip()

    # extract files written — tool_result format: "[write_file]\nWritten N lines to path"
    files_written: list[str] = []
    for msg in conversation:
        if msg["role"] == "tool_result":
            for m in _re.finditer(r'Written \d+ lines to (.+)', msg["content"]):
                files_written.append(m.group(1).strip())
            for m in _re.finditer(r'Appended \d+ lines to (.+)', msg["content"]):
                files_written.append(m.group(1).strip())

    # build a clean summary: list written files + final prose
    if files_written:
        summary_lines = [f"Files written: {', '.join(files_written)}"]
        if result_clean:
            summary_lines.append(result_clean[:600])
        content = "\n".join(summary_lines)
    else:
        content = result_clean or f"[worker {subtask['id']} completed: {subtask['task']}]"

    return Artifact(
        group_id=group_plan.id,
        name=subtask.get("artifact_name", group_plan.artifact_name),
        content=content,
        files=files_written,
    )


# ── group runner ──────────────────────────────────────────────────────────────

async def run_group(
    plan: GroupPlan,
    master_task: str,
    dep_artifacts: list[Artifact],
    model_id: str,
    context: str,
    memory_md: str,
    user_md: str,
    deepcode_md: str,
    project_memory_md: str,
    semaphore: asyncio.Semaphore,
    progress: "ProgressTracker",
    swarm_state: Optional["SwarmState"] = None,
) -> GroupResult:
    t0 = time.time()
    progress.set_status(plan.id, Status.RUNNING)
    if swarm_state is not None:
        swarm_state.group_status[plan.id] = Status.RUNNING

    try:
        if plan.depth > 0:
            # Recursive: this group is too big — spawn sub-groups via a sub-leader
            sub_agents = plan.size * MAX_CHILDREN
            renderer.print_info(f"  [ultracode] Group {plan.id} ({plan.role}): spawning sub-swarm ({sub_agents} agents)")
            sub_context = context
            for a in dep_artifacts:
                sub_context += f"\n[{a.name}]\n{a.content[:2000]}\n"

            sub_plans, _sub_total = await run_leader(
                f"{plan.goal}\n\nContext from parent task: {master_task}",
                model_id, sub_context, memory_md, user_md, deepcode_md
            )

            orchestrator = UltraCodeOrchestrator(
                task=plan.goal,
                model_id=model_id,
                memory_md=memory_md,
                user_md=user_md,
                deepcode_md=deepcode_md,
                project_memory_md=project_memory_md,
                _parent_context=sub_context,
                _parent_semaphore=semaphore,
            )
            sub_results = await orchestrator.run_from_plans(sub_plans, swarm_state=swarm_state)
            # Aggregate sub-results into one artifact
            combined = "\n\n".join(
                f"[{r.artifact.name}]\n{r.artifact.content}"
                for r in sub_results if r.status == Status.DONE
            )
            all_files = [f for r in sub_results for f in r.artifact.files]
            artifact = Artifact(
                group_id=plan.id,
                name=plan.artifact_name,
                content=combined[:_CONTEXT_BUDGET * 2],
                files=all_files,
            )

        else:
            # Normal group: manager decomposes → workers run in parallel
            group_context = context
            if swarm_state is not None and swarm_state.extra_notes:
                notes = "\n".join(f"- {n}" for n in swarm_state.extra_notes)
                group_context = f"{context}\n\n[Live user notes added during the run — incorporate these]\n{notes}"
            subtasks = await run_manager(plan, master_task, dep_artifacts, model_id, group_context)

            worker_deepcode = deepcode_md
            if swarm_state is not None and swarm_state.extra_notes:
                notes = "\n".join(f"- {n}" for n in swarm_state.extra_notes)
                worker_deepcode = f"{deepcode_md}\n\n[Live user notes added during the run — incorporate these]\n{notes}".strip()

            worker_tasks = []
            for i, st in enumerate(subtasks, 1):
                wid = f"{plan.id}:W{i}"
                async def _worker(st=st, wid=wid):
                    async with semaphore:
                        return await run_worker(
                            st, plan, master_task, dep_artifacts,
                            model_id, memory_md, user_md, worker_deepcode, project_memory_md,
                            swarm_state=swarm_state, agent_id=wid,
                        )
                worker_tasks.append(asyncio.create_task(_worker()))

            worker_results: list[Artifact] = await asyncio.gather(*worker_tasks, return_exceptions=True)

            valid = [r for r in worker_results if isinstance(r, Artifact)]
            combined_parts = []
            for a in valid:
                # content is already clean (stripped in run_worker), just summarise
                combined_parts.append(f"[{a.name}] {a.content[:300]}")
            combined = "\n".join(combined_parts)
            all_files = [f for a in valid for f in a.files]
            # top-level group artifact: list all files + brief per-worker summary
            files_line = f"Files: {', '.join(all_files)}" if all_files else "No files written"
            artifact = Artifact(
                group_id=plan.id,
                name=plan.artifact_name,
                content=f"{files_line}\n{combined}"[:_CONTEXT_BUDGET * 2],
                files=all_files,
            )

        elapsed = time.time() - t0
        progress.set_status(plan.id, Status.DONE)
        if swarm_state is not None:
            swarm_state.group_status[plan.id] = Status.DONE
        renderer.print_info(f"  [ultracode] ✓ {plan.id} ({plan.role}) — {elapsed:.1f}s")
        return GroupResult(group_id=plan.id, artifact=artifact, status=Status.DONE, elapsed=elapsed)

    except Exception as e:
        elapsed = time.time() - t0
        progress.set_status(plan.id, Status.FAILED)
        if swarm_state is not None:
            swarm_state.group_status[plan.id] = Status.FAILED
        renderer.print_error(f"  [ultracode] ✗ {plan.id} ({plan.role}): {e}")
        return GroupResult(
            group_id=plan.id,
            artifact=Artifact(plan.id, plan.artifact_name, f"[FAILED: {e}]"),
            status=Status.FAILED,
            elapsed=elapsed,
        )


# ── progress tracker ──────────────────────────────────────────────────────────

class ProgressTracker:
    def __init__(self, plans: list[GroupPlan]):
        self._statuses: dict[str, Status] = {p.id: Status.PENDING for p in plans}
        self._roles: dict[str, str] = {p.id: p.role for p in plans}
        self._lock = asyncio.Lock()

    def set_status(self, gid: str, status: Status):
        self._statuses[gid] = status

    def summary(self) -> str:
        counts = {s: 0 for s in Status}
        for s in self._statuses.values():
            counts[s] += 1
        return (
            f"pending={counts[Status.PENDING]} "
            f"running={counts[Status.RUNNING]} "
            f"done={counts[Status.DONE]} "
            f"failed={counts[Status.FAILED]}"
        )


# ── DAG scheduler ─────────────────────────────────────────────────────────────

async def run_dag(
    plans: list[GroupPlan],
    master_task: str,
    model_id: str,
    context: str,
    memory_md: str,
    user_md: str,
    deepcode_md: str,
    project_memory_md: str,
    semaphore: asyncio.Semaphore,
    progress: ProgressTracker,
    swarm_state: Optional["SwarmState"] = None,
) -> list[GroupResult]:
    """
    Execute groups in dependency order. As soon as all deps of a group are done,
    fire it — don't wait for a full "wave". True streaming DAG execution.
    """
    plan_map = {p.id: p for p in plans}
    results: dict[str, GroupResult] = {}
    running: dict[str, asyncio.Task] = {}
    pending = set(p.id for p in plans)

    _settled = {Status.DONE, Status.FAILED}

    def _deps_satisfied(gid: str) -> bool:
        # fires when all deps have any settled result — failed deps don't block
        for dep in plan_map[gid].depends_on:
            if dep not in results or results[dep].status not in _settled:
                return False
        return True

    def _get_dep_artifacts(gid: str) -> list[Artifact]:
        arts = []
        for dep in plan_map[gid].depends_on:
            if dep in results:
                r = results[dep]
                if r.status == Status.FAILED:
                    # inject a warning note so the group knows the dep failed
                    arts.append(Artifact(
                        group_id=dep,
                        name=r.artifact.name,
                        content=f"[WARNING: dependency {dep} ({r.artifact.name}) failed or was skipped. "
                                f"Proceed with best judgment — do not depend on its output.]",
                        files=[],
                    ))
                else:
                    arts.append(r.artifact)
        return arts

    async def _run_one(gid: str) -> str:
        plan = plan_map[gid]
        dep_arts = _get_dep_artifacts(gid)
        result = await run_group(
            plan, master_task, dep_arts, model_id, context,
            memory_md, user_md, deepcode_md, project_memory_md,
            semaphore, progress, swarm_state=swarm_state,
        )
        results[gid] = result
        return gid

    # task -> gid so we can reap finished groups after each wait
    task_gid: dict[asyncio.Task, str] = {}

    while pending or running:
        # fire every group whose deps are all settled
        for gid in list(pending):
            if _deps_satisfied(gid):
                pending.discard(gid)
                t = asyncio.create_task(_run_one(gid))
                running[gid] = t
                task_gid[t] = gid

        if not running:
            # nothing in flight but groups remain → unresolvable (cycle / all-failed deps)
            if pending:
                for gid in list(pending):
                    renderer.print_error(f"  [ultracode] Unresolvable deps for {gid} — skipping")
                    results[gid] = GroupResult(
                        group_id=gid,
                        artifact=Artifact(gid, plan_map[gid].artifact_name, "[SKIPPED: unresolvable deps]"),
                        status=Status.FAILED,
                    )
                    pending.discard(gid)
            break

        # wait for at least one running group to finish, then reap all that are done.
        # Awaiting the tasks themselves is race-free — no shared Event to clobber.
        done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            gid = task_gid.pop(t, None)
            if gid is not None:
                running.pop(gid, None)

    return list(results.values())


# ── main orchestrator ─────────────────────────────────────────────────────────

class UltraCodeOrchestrator:
    def __init__(
        self,
        task: str,
        model_id: str,
        memory_md: str = "",
        user_md: str = "",
        deepcode_md: str = "",
        project_memory_md: str = "",
        _parent_context: str = "",
        _parent_semaphore: Optional[asyncio.Semaphore] = None,
        swarm_state: Optional["SwarmState"] = None,
    ):
        self.task = task
        self.total_agents = DEFAULT_AGENTS  # updated once leader decides
        self.model_id = model_id
        self.memory_md = memory_md
        self.user_md = user_md
        self.deepcode_md = deepcode_md
        self.project_memory_md = project_memory_md
        self._context = _parent_context or gather_project_context()
        self._semaphore = _parent_semaphore
        self.swarm_state = swarm_state

    async def run(self) -> list[GroupResult]:
        renderer.print_info("[ultracode] Leader planning swarm...")
        plans, total_agents = await run_leader(
            self.task, self.model_id,
            self._context, self.memory_md, self.user_md, self.deepcode_md,
        )
        self.total_agents = total_agents
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max(1, total_agents))
        renderer.print_info(f"[ultracode] Leader chose {total_agents} agents")
        if self.swarm_state is not None:
            self.swarm_state.task = self.task
            self.swarm_state.plans = plans
            for p in plans:
                self.swarm_state.group_status[p.id] = Status.PENDING
        return await self.run_from_plans(plans, swarm_state=self.swarm_state)

    async def run_from_plans(self, plans: list[GroupPlan], swarm_state: Optional["SwarmState"] = None) -> list[GroupResult]:
        swarm_state = swarm_state or self.swarm_state
        if self._semaphore is None:
            total_workers_est = max(1, sum(p.size for p in plans))
            self._semaphore = asyncio.Semaphore(total_workers_est)
        progress = ProgressTracker(plans)
        n_groups = len(plans)
        total_workers = sum(p.size for p in plans)
        renderer.print_info(
            f"[ultracode] {n_groups} groups · ~{total_workers} workers · "
            f"DAG depth {_dag_depth(plans)}"
        )

        results = await run_dag(
            plans, self.task, self.model_id, self._context,
            self.memory_md, self.user_md, self.deepcode_md,
            self.project_memory_md, self._semaphore, progress,
            swarm_state=swarm_state,
        )

        done = [r for r in results if r.status == Status.DONE]
        failed = [r for r in results if r.status == Status.FAILED]
        renderer.print_info(
            f"[ultracode] Complete — {len(done)} groups done, {len(failed)} failed"
        )
        return results

    def synthesize(self, results: list[GroupResult]) -> str:
        """Combine all group artifacts into a final summary."""
        parts = ["# UltraCode Swarm Results\n"]
        for r in sorted(results, key=lambda x: x.group_id):
            status_icon = "✓" if r.status == Status.DONE else "✗"
            parts.append(f"\n## {status_icon} {r.group_id}: {r.artifact.name}")
            if r.artifact.files:
                parts.append(f"Files: {', '.join(r.artifact.files)}")
            parts.append(r.artifact.content[:1000])
        return "\n".join(parts)


def _dag_depth(plans: list[GroupPlan]) -> int:
    """Compute longest path in the DAG."""
    plan_map = {p.id: p for p in plans}
    memo: dict[str, int] = {}

    def depth(gid: str) -> int:
        if gid in memo:
            return memo[gid]
        p = plan_map.get(gid)
        if not p or not p.depends_on:
            memo[gid] = 0
            return 0
        d = 1 + max(depth(dep) for dep in p.depends_on if dep in plan_map)
        memo[gid] = d
        return d

    return max((depth(p.id) for p in plans), default=0)
