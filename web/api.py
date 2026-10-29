"""FastAPI REST API router for the NAO Web Dashboard."""

from __future__ import annotations

import json
import os
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

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


# --- Pydantic Request Models ---

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


# --- Endpoints ---

@api_router.get("/system")
def get_system_telemetry() -> dict:
    """Return live system telemetry, resources, and project configuration."""
    services = get_services()
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
        "project_root": str(services.project_root),
        "default_repo_path": services.config.default_repo_path,
        "concurrency_limit": services.config.resources.max_concurrent_agents,
        "min_available_ram_percent": services.config.resources.min_available_ram_percent,
        "resources": stats,
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
    """List all tasks stored in artifacts."""
    manager = get_manager()
    task_ids = manager.list_tasks()
    tasks = []
    for tid in sorted(task_ids, reverse=True):
        task = manager.get(tid)
        if not task:
            continue
        if status and task.state.value.lower() != status.lower():
            continue
        
        # Check if currently running in background
        is_running = tid in _running_tasks
        tasks.append({
            "task_id": task.task_id,
            "title": task.title,
            "description": task.description,
            "recipe": task.recipe,
            "state": task.state.value,
            "is_running": is_running,
            "repo_path": task.repo_path,
            "acceptance_criteria_count": len(task.acceptance_criteria),
            "created_at": task.created_at.isoformat() if hasattr(task, "created_at") and task.created_at else None,
            "updated_at": task.updated_at.isoformat() if hasattr(task, "updated_at") and task.updated_at else None,
            "current_step": task.current_step,
            "jobs_count": len(task.jobs),
            "fix_attempt": task.fix_attempt,
        })
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
        return {
            "success": True,
            "task_id": task.task_id,
            "task": task.to_dict(),
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

    # Load diffs and contexts
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

    # Full recipe definition to visualize the stepper
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

    return {
        "task": task.to_dict(),
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
        return {
            "success": True,
            "state": state.to_dict(),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "repo_path": target_repo,
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
        # Load from all task directories
        audit_dir = services.audit.base_dir
        if audit_dir.exists():
            for task_folder in audit_dir.iterdir():
                if task_folder.is_dir():
                    ndjson_file = task_folder / "events.ndjson"
                    if ndjson_file.exists():
                        try:
                            lines = ndjson_file.read_text(encoding="utf-8").splitlines()
                            for line in lines[-50:]:  # take last 50 per task
                                line = line.strip()
                                if line:
                                    events.append(json.loads(line))
                        except Exception:
                            continue

    events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return events[:limit]
