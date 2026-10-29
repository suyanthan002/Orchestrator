"""FastAPI REST API router for the NAO Web Dashboard."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from nao.paths import CONFIG_DIR
from nao.services import NaoServices, build_services
from nao.task_manager import TaskManager
from nao.workflow.recipes import RECIPES, list_recipes
from nao.models.enums import TaskState, Permission

# Shared router
api_router = APIRouter(prefix="/api")

# Singleton services and task manager
_services: Optional[NaoServices] = None
_manager: Optional[TaskManager] = None
_running_tasks: Dict[str, dict] = {}
_lock = threading.Lock()

USERS_FILE = CONFIG_DIR / "users.json"


def get_services() -> NaoServices:
    global _services
    if _services is None:
        _services = build_services()
    return _services


def get_manager() -> TaskManager:
    global _manager
    if _manager is None:
        _manager = TaskManager(get_services())
    return _manager


def sanitize_display_path(p: Optional[str]) -> str:
    """Sanitize internal/personal directory paths so they are not exposed in the UI."""
    if not p:
        return "Workspace: Orchestrator"
    path_str = str(p).replace("\\", "/")
    # Remove any sensitive user directory prefixes
    if "Users/" in path_str or "OneDrive/" in path_str or "Desktop/" in path_str or "home/" in path_str:
        return "Workspace: Orchestrator"
    try:
        parts = Path(p).parts
        if parts:
            return f"Workspace: {parts[-1]}"
    except Exception:
        pass
    return "Workspace: Orchestrator"


# --- Authentication Storage & Helpers ---

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _load_users() -> Dict[str, dict]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    initial_users = {
        "admin": {
            "username": "admin",
            "password_hash": _hash_password("admin123"),
            "full_name": "Admin",
            "role": "Admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "user": {
            "username": "user",
            "password_hash": _hash_password("user123"),
            "full_name": "User",
            "role": "User",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    if not USERS_FILE.exists():
        USERS_FILE.write_text(json.dumps(initial_users, indent=2), encoding="utf-8")
        return initial_users
    try:
        users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        changed = False
        if "admin" not in users:
            users["admin"] = initial_users["admin"]
            changed = True
        if "user" not in users:
            users["user"] = initial_users["user"]
            changed = True
        if changed:
            _save_users(users)
        return users
    except Exception:
        return initial_users


def _save_users(users: Dict[str, dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


# --- Pydantic Request Models ---

class UserLoginRequest(BaseModel):
    username: str
    password: str


class UserRegisterRequest(BaseModel):
    username: str
    password: str
    full_name: str
    role: str = "Lead Engineer"


class TaskCreateRequest(BaseModel):
    title: str = ""
    description: str
    recipe: str = "feature_implementation"
    acceptance_criteria: List[str] = Field(default_factory=list)
    repo_path: Optional[str] = None
    granted_permissions: List[str] = Field(default_factory=list)
    routing_overrides: Dict[str, str] = Field(default_factory=dict)
    max_fix_attempts: int = 2
    max_jobs_per_role: int = 1
    test_command: Optional[str] = None


class ApprovalDecisionRequest(BaseModel):
    approval_id: Optional[str] = None
    subject: Optional[str] = None
    action: str  # "approve" or "reject"
    by: str = "Chief Architect"
    note: Optional[str] = None


# --- Authentication Endpoints ---

@api_router.post("/auth/login")
def login(req: UserLoginRequest) -> dict:
    users = _load_users()
    username = req.username.strip().lower()
    user = users.get(username)
    if not user or user.get("password_hash") != _hash_password(req.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {
        "success": True,
        "user": {
            "username": user["username"],
            "full_name": user.get("full_name", user["username"]),
            "role": user.get("role", "Developer"),
        },
        "token": f"nao-session-{username}-{int(datetime.now(timezone.utc).timestamp())}",
    }


@api_router.post("/auth/register")
def register(req: UserRegisterRequest) -> dict:
    username = req.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if username == "admin" or req.role.strip().lower() == "admin":
        raise HTTPException(status_code=403, detail="Admin account is pre-configured and cannot be registered.")
    if len(req.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    users = _load_users()
    if username in users:
        raise HTTPException(status_code=400, detail="Username already exists")

    new_user = {
        "username": username,
        "password_hash": _hash_password(req.password),
        "full_name": req.full_name.strip() or username.title(),
        "role": "User",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    users[username] = new_user
    _save_users(users)

    return {
        "success": True,
        "user": {
            "username": new_user["username"],
            "full_name": new_user["full_name"],
            "role": new_user["role"],
        },
        "token": f"nao-session-{username}-{int(datetime.now(timezone.utc).timestamp())}",
    }


@api_router.get("/auth/me")
def get_current_user() -> dict:
    return {
        "authenticated": True,
        "user": {
            "username": "admin",
            "full_name": "Chief Architect",
            "role": "Chief Architect",
        }
    }


# --- System & Telemetry Endpoints ---

@api_router.get("/system")
def get_system_telemetry() -> dict:
    """Return live system telemetry, RAM/disk storage breakdown, and sanitized config."""
    services = get_services()
    
    ram_total_gb = None
    ram_used_gb = None
    ram_free_gb = None
    ram_percent = None

    disk_total_gb = None
    disk_used_gb = None
    disk_free_gb = None
    disk_percent = None
    cpu_percent = None

    try:
        import psutil
        vm = psutil.virtual_memory()
        du = psutil.disk_usage('.')
        ram_total_gb = round(vm.total / (1024 ** 3), 2)
        ram_used_gb = round(vm.used / (1024 ** 3), 2)
        ram_free_gb = round(vm.available / (1024 ** 3), 2)
        ram_percent = float(vm.percent)

        disk_total_gb = round(du.total / (1024 ** 3), 2)
        disk_used_gb = round(du.used / (1024 ** 3), 2)
        disk_free_gb = round(du.free / (1024 ** 3), 2)
        disk_percent = float(du.percent)
        cpu_percent = float(psutil.cpu_percent(interval=None))
    except Exception:
        pass

    try:
        stats = services.resources.stats().to_dict()
    except Exception as e:
        stats = {
            "max_concurrent_agents": 1,
            "active_jobs": 0,
            "available_ram_percent": None,
            "disk_free_gb": None,
            "cpu_percent": None,
            "error": str(e),
        }

    return {
        "project_root": "Workspace: Orchestrator",
        "default_repo_path": "Workspace: Orchestrator",
        "concurrency_limit": services.config.resources.max_concurrent_agents,
        "min_available_ram_percent": services.config.resources.min_available_ram_percent,
        "resources": stats,
        "telemetry": {
            "ram_total_gb": ram_total_gb,
            "ram_used_gb": ram_used_gb,
            "ram_free_gb": ram_free_gb,
            "ram_percent": ram_percent,
            "disk_total_gb": disk_total_gb,
            "disk_used_gb": disk_used_gb,
            "disk_free_gb": disk_free_gb,
            "disk_percent": disk_percent,
            "cpu_percent": cpu_percent,
        },
        "running_tasks_count": len(_running_tasks),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@api_router.get("/agents")
def get_agents() -> List[dict]:
    """Return all configured agents with live doctor diagnostics."""
    services = get_services()
    return services.registry.doctor()


@api_router.post("/agents/doctor")
def run_agent_doctor() -> List[dict]:
    """Force an active re-discovery of all configured agents."""
    services = get_services()
    return services.registry.doctor()


@api_router.get("/recipes")
def get_recipes() -> List[dict]:
    """Return list of all workflow recipes and their steps."""
    result = []
    for r in list_recipes():
        steps = [
            {
                "kind": s.kind,
                "role": s.role,
                "approval": s.approval,
                "description": s.description,
            }
            for s in r.steps
        ]
        result.append({
            "name": r.name,
            "description": r.description,
            "steps": steps,
            "step_count": len(steps),
            "mutating": r.mutating,
            "requires_review": r.requires_review,
            "default_permissions": list(r.default_permissions),
        })
    return result


@api_router.get("/tasks")
def list_tasks(status: Optional[str] = None) -> List[dict]:
    """List all tasks stored in artifacts, sorted with newest first."""
    manager = get_manager()
    task_ids = manager.list_tasks()
    tasks = []
    for tid in task_ids:
        task = manager.get(tid)
        if not task:
            continue
        if status and task.state.value.lower() != status.lower():
            continue
        
        is_running = tid in _running_tasks
        jobs = manager.jobs(tid)
        tasks.append({
            "task_id": task.task_id,
            "title": task.title,
            "description": task.description,
            "recipe": task.recipe,
            "state": task.state.value,
            "is_running": is_running,
            "repo_path": sanitize_display_path(task.repo_path),
            "acceptance_criteria_count": len(task.acceptance_criteria),
            "created_at": task.created_at.isoformat() if hasattr(task, "created_at") and task.created_at else None,
            "updated_at": task.updated_at.isoformat() if hasattr(task, "updated_at") and task.updated_at else None,
            "current_step": task.metadata.get("current_step", 0),
            "jobs_count": len(jobs),
            "fix_attempt": task.metadata.get("fix_attempt", 0),
        })
    # Sort strictly by creation timestamp descending so newly created tasks appear immediately at the top
    tasks.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    return tasks


@api_router.post("/tasks")
def create_task(req: TaskCreateRequest) -> dict:
    """Create a new orchestration task."""
    manager = get_manager()
    metadata: Dict[str, Any] = {}
    if req.test_command:
        metadata["test_command"] = req.test_command

    try:
        task = manager.create_task(
            description=req.description,
            title=req.title,
            recipe=req.recipe,
            acceptance_criteria=req.acceptance_criteria,
            repo_path=req.repo_path,
            granted_permissions=req.granted_permissions if req.granted_permissions else None,
            routing_overrides=req.routing_overrides,
            max_fix_attempts=req.max_fix_attempts,
            max_jobs_per_role=req.max_jobs_per_role,
            metadata=metadata,
        )
        task_dict = task.to_dict()
        task_dict["repo_path"] = sanitize_display_path(task.repo_path)
        return {
            "success": True,
            "task_id": task.task_id,
            "task": task_dict,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@api_router.get("/tasks/{task_id}")
def get_task_details(task_id: str) -> dict:
    """Return comprehensive task details including jobs, diffs, tests, findings, context, and approvals."""
    services = get_services()
    manager = get_manager()
    task = manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    jobs = manager.jobs(task_id)
    findings = manager.findings(task_id)
    evidence = manager.evidence(task_id)
    approvals = [a.to_dict() for a in services.approvals.all(task_id)]
    audit_events = manager.audit_events(task_id)

    diffs = {}
    contexts = {}
    diffs_dir = services.artifacts.task_dir(task_id) / "diffs"
    if diffs_dir.exists():
        for p in diffs_dir.glob("*.diff"):
            diffs[p.stem] = p.read_text(encoding="utf-8")

    contexts_dir = services.artifacts.task_dir(task_id) / "contexts"
    if contexts_dir.exists():
        for p in contexts_dir.glob("*.txt"):
            contexts[p.stem] = p.read_text(encoding="utf-8")

    recipe_def = RECIPES.get(task.recipe)
    recipe_steps = []
    if recipe_def:
        for s in recipe_def.steps:
            recipe_steps.append({
                "kind": s.kind,
                "role": s.role,
                "approval": s.approval,
                "description": s.description,
            })

    task_dict = task.to_dict()
    task_dict["repo_path"] = sanitize_display_path(task.repo_path)

    return {
        "task": task_dict,
        "is_running": task_id in _running_tasks,
        "recipe_steps": recipe_steps,
        "jobs": jobs,
        "diffs": diffs,
        "contexts": contexts,
        "evidence": evidence,
        "findings": findings,
        "approvals": approvals,
        "audit_events": audit_events,
        "status_summary": manager.status(task_id),
    }


def _run_task_worker(task_id: str):
    """Background worker thread to run a task to completion or next gate."""
    manager = get_manager()
    try:
        with _lock:
            _running_tasks[task_id] = {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            }
        result = manager.run_task(task_id)
        with _lock:
            if task_id in _running_tasks:
                _running_tasks[task_id]["status"] = "finished"
                _running_tasks[task_id]["result"] = result.to_dict()
    except Exception as e:
        with _lock:
            if task_id in _running_tasks:
                _running_tasks[task_id]["status"] = "error"
                _running_tasks[task_id]["error"] = str(e)
    finally:
        with _lock:
            _running_tasks.pop(task_id, None)


@api_router.post("/tasks/{task_id}/run")
def run_task(task_id: str) -> dict:
    """Trigger execution of a task in the background."""
    manager = get_manager()
    task = manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    with _lock:
        if task_id in _running_tasks:
            return {"success": False, "message": "Task is already executing", "task_id": task_id}

    worker = threading.Thread(target=_run_task_worker, args=(task_id,), daemon=True)
    worker.start()

    return {
        "success": True,
        "message": f"Task '{task_id}' execution started",
        "task_id": task_id,
    }


@api_router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict:
    """Cancel a running task and its associated subprocess tree."""
    manager = get_manager()
    success = manager.cancel(task_id)
    with _lock:
        _running_tasks.pop(task_id, None)
    return {"success": success, "task_id": task_id}


# --- Human Approvals Center ---

@api_router.get("/approvals")
def list_all_approvals() -> List[dict]:
    """List all approvals across all tasks with current state."""
    services = get_services()
    manager = get_manager()
    all_approvals = []
    
    for tid in manager.list_tasks():
        approvals = services.approvals.all(tid)
        task = manager.get(tid)
        for a in approvals:
            item = a.to_dict()
            item["task_title"] = task.title if task else tid
            item["task_recipe"] = task.recipe if task else ""
            item["task_state"] = task.state.value if task else ""
            all_approvals.append(item)

    all_approvals.sort(
        key=lambda x: (x.get("state") != "pending", x.get("requested_at", "")),
        reverse=False
    )
    return all_approvals


@api_router.post("/approvals/{task_id}/decide")
def decide_approval(task_id: str, req: ApprovalDecisionRequest) -> dict:
    """Grant or deny a human approval gate."""
    services = get_services()
    manager = get_manager()
    approvals = services.approvals.all(task_id)
    
    target_id = req.approval_id
    if not target_id and req.subject:
        for a in approvals:
            if a.subject == req.subject and a.state.value == "pending":
                target_id = a.approval_id
                break
    if not target_id:
        pending = services.approvals.pending(task_id)
        if pending:
            target_id = pending[0].approval_id

    if not target_id:
        raise HTTPException(status_code=404, detail="No matching pending approval found")

    try:
        if req.action.lower() in ("approve", "grant"):
            result = manager.approve(task_id, target_id, approver=req.by, note=req.note)
        elif req.action.lower() in ("reject", "deny"):
            result = manager.reject(task_id, target_id, approver=req.by, note=req.note)
        else:
            raise HTTPException(status_code=400, detail=f"Invalid action '{req.action}'")
        return {"success": True, "approval": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Git & Workspace Inspection ---

@api_router.get("/git/status")
def get_git_status(repo: Optional[str] = None) -> dict:
    """Inspect repository git state, worktrees, dirty files, and branch."""
    services = get_services()
    target_repo = repo or services.config.default_repo_path or str(services.project_root)
    git_mgr = services.git_factory(target_repo)
    try:
        state = git_mgr.state()
        state_dict = state.to_dict()
        state_dict["repo_path"] = "Workspace: Orchestrator"
        return {
            "success": True,
            "state": state_dict,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "repo_path": "Workspace: Orchestrator",
        }


# --- Global Audit Event Stream ---

@api_router.get("/audit")
def get_audit_feed(limit: int = Query(default=100, le=500), task_id: Optional[str] = None) -> List[dict]:
    """Return chronological audit events from memory and storage."""
    services = get_services()
    manager = get_manager()
    events = []

    if task_id:
        events = manager.audit_events(task_id)
    else:
        audit_dir = services.audit.base_dir
        if audit_dir.exists():
            for task_folder in audit_dir.iterdir():
                if task_folder.is_dir():
                    ndjson_file = task_folder / "events.ndjson"
                    if ndjson_file.exists():
                        try:
                            lines = ndjson_file.read_text(encoding="utf-8").splitlines()
                            for line in lines[-50:]:
                                line = line.strip()
                                if line:
                                    events.append(json.loads(line))
                        except Exception:
                            continue

    events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return events[:limit]
