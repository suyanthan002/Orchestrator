"""Central path discovery.

NAO resolves its project root once, from this file's location, so every
subsystem agrees on where ``config/``, ``runs/``, ``audit/`` and ``tasks/``
live. Nothing here is machine-specific.
"""

from __future__ import annotations

from pathlib import Path

# nao/paths.py -> nao/ -> project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = PROJECT_ROOT / "config"
DOC_DIR = PROJECT_ROOT / "doc"
RUNS_DIR = PROJECT_ROOT / "runs"
TASKS_DIR = PROJECT_ROOT / "tasks"
AUDIT_DIR = PROJECT_ROOT / "audit"
LOGS_DIR = PROJECT_ROOT / "logs"

AGENTS_CONFIG = CONFIG_DIR / "agents.json"
NAO_CONFIG = CONFIG_DIR / "nao.json"


def ensure_runtime_dirs() -> None:
    """Create runtime output directories if they are missing."""
    for directory in (RUNS_DIR, TASKS_DIR, AUDIT_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
