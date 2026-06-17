from __future__ import annotations
import json
import os
from pathlib import Path
from datetime import datetime

DATA_DIR = Path.home() / ".deepcodev3"
HISTORY_FILE = DATA_DIR / "history.json"
MEMORY_FILE = DATA_DIR / "memory.json"
CONFIG_FILE = DATA_DIR / "config.json"
MEMORY_MD_FILE = DATA_DIR / "MEMORY.md"       # global personal facts
USER_MD_FILE = DATA_DIR / "USER.md"           # user identity/profile
PERMISSIONS_FILE = DATA_DIR / "permissions.json"

MEMORY_MD_MAX_CHARS = 3200   # ~800 tokens
USER_MD_MAX_CHARS = 2000     # ~500 tokens
PROJECT_MEMORY_MAX_CHARS = 2000  # ~500 tokens
COMPRESS_AT = 0.80           # compress when file hits 80% of max


def ensure_dir():
    DATA_DIR.mkdir(exist_ok=True)


def load_history() -> list[dict]:
    ensure_dir()
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(sessions: list[dict]):
    ensure_dir()
    HISTORY_FILE.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")


def load_memory() -> list[str]:
    ensure_dir()
    if not MEMORY_FILE.exists():
        return []
    try:
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_memory(facts: list[str]):
    ensure_dir()
    MEMORY_FILE.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")


def load_memory_md() -> str:
    ensure_dir()
    if not MEMORY_MD_FILE.exists():
        # Migrate from memory.json if exists
        facts = load_memory()
        if facts:
            content = "\n".join(f"- {f}" for f in facts)
            MEMORY_MD_FILE.write_text(content, encoding="utf-8")
            return content
        return ""
    try:
        return MEMORY_MD_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_memory_md(content: str):
    ensure_dir()
    MEMORY_MD_FILE.write_text(content[:MEMORY_MD_MAX_CHARS], encoding="utf-8")


def load_user_md() -> str:
    ensure_dir()
    if not USER_MD_FILE.exists():
        return ""
    try:
        return USER_MD_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_user_md(content: str):
    ensure_dir()
    USER_MD_FILE.write_text(content[:USER_MD_MAX_CHARS], encoding="utf-8")


def load_config() -> dict:
    ensure_dir()
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict):
    ensure_dir()
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


SOUL_FILE = DATA_DIR / "SOUL.md"
SOUL_MAX_CHARS = 1024


def load_soul_md() -> str:
    """Load SOUL.md from ~/.deepcodev3/SOUL.md — global personality, hard-capped at 1024 chars."""
    ensure_dir()
    if not SOUL_FILE.exists():
        return ""
    try:
        return SOUL_FILE.read_text(encoding="utf-8").strip()[:SOUL_MAX_CHARS]
    except Exception:
        return ""


def save_soul_md(content: str):
    ensure_dir()
    SOUL_FILE.write_text(content[:SOUL_MAX_CHARS], encoding="utf-8")


def delete_soul_md():
    if SOUL_FILE.exists():
        SOUL_FILE.unlink()


def load_deepcode_md() -> str:
    """Load DEEPCODE.md from current working directory if it exists."""
    p = Path.cwd() / "DEEPCODE.md"
    if p.exists():
        try:
            return p.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return ""


def _project_memory_file() -> Path:
    return Path.cwd() / ".deepcodev3" / "MEMORY.md"


def load_project_memory_md() -> str:
    """Load project-local MEMORY.md from <cwd>/.deepcodev3/MEMORY.md."""
    p = _project_memory_file()
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_project_memory_md(content: str):
    p = _project_memory_file()
    p.parent.mkdir(exist_ok=True)
    p.write_text(content[:PROJECT_MEMORY_MAX_CHARS], encoding="utf-8")


def needs_compression(content: str, max_chars: int) -> bool:
    return len(content) >= max_chars * COMPRESS_AT


def new_session(model_id: str) -> dict:
    return {
        "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "model": model_id,
        "created": datetime.now().isoformat(),
        "messages": [],
    }


# --- Permission rules persistence ---

def load_permission_rules() -> list[dict]:
    """Load persisted deny/allow rules: [{"tool": str, "pattern": str|None, "decision": "allow"|"deny"}]."""
    ensure_dir()
    if not PERMISSIONS_FILE.exists():
        return []
    try:
        data = json.loads(PERMISSIONS_FILE.read_text(encoding="utf-8"))
        return data.get("rules", [])
    except Exception:
        return []


def save_permission_rules(rules: list[dict]):
    ensure_dir()
    PERMISSIONS_FILE.write_text(json.dumps({"rules": rules}, ensure_ascii=False, indent=2), encoding="utf-8")
