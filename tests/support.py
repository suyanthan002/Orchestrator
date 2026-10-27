"""Shared test support: scripted adapters, temp repos and service graphs.

Tests never invoke a real cloud agent. External agents are replaced with a
scripted test double that implements the same adapter contract, so the whole
orchestration pipeline can be exercised deterministically and offline.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from nao.adapters.base import AgentAdapter, Availability
from nao.audit import AuditLogger
from nao.config import AgentConfig, ResourceConfig, load_config
from nao.models.agent_contract import AgentRequest, AgentResponse
from nao.launch import LaunchAuthority
from nao.models.enums import Role
from nao.process import InternalProcessService, ProcessRunner
from nao.services import NaoServices, build_services


class ScriptedAdapter(AgentAdapter):
    """Adapter test double: returns scripted responses per role."""

    name = "scripted"
    #: Role -> callable(AgentRequest) -> AgentResponse
    handlers: dict[Role, Callable[[AgentRequest], AgentResponse]] = {}
    #: Records every request it received (shared across instances by default).
    requests: list[AgentRequest] = []
    supported_permissions_override: Optional[frozenset] = None

    def enforceable_permissions(self) -> frozenset:
        if self.supported_permissions_override is not None:
            return self.supported_permissions_override
        from nao.models.enums import Permission
        return frozenset(Permission)

    def availability(self) -> Availability:
        return Availability(True, "scripted test double")

    def parse(self, request: AgentRequest, result) -> AgentResponse:  # noqa: D102
        return AgentResponse(job_id=request.job_id, agent=request.agent)

    def execute(self, request: AgentRequest) -> AgentResponse:
        self.requests.append(request)
        handler = self.handlers.get(request.role)
        if handler is None:
            return make_response(request, text="ok")
        return handler(request)


def make_response(
    request: AgentRequest,
    text: Optional[str] = None,
    structured: Optional[dict[str, Any]] = None,
    status: str = "succeeded",
    errors: Optional[list[dict]] = None,
    session_id: Optional[str] = None,
) -> AgentResponse:
    return AgentResponse(
        job_id=request.job_id,
        agent=request.agent,
        status=status,
        response=text,
        structured_output=structured,
        exit_code=0 if status == "succeeded" else 1,
        duration_seconds=0.01,
        errors=list(errors or []),
        session_id=session_id,
    )


def make_adapter_class(handlers: dict) -> type:
    """Build a fresh ScriptedAdapter subclass bound to a handler map."""
    adapter_class = type(
        f"ScriptedAdapter_{uuid4().hex[:6]}", (ScriptedAdapter,), {}
    )
    adapter_class.handlers = dict(handlers)
    adapter_class.requests = []
    return adapter_class


def scripted_services(
    root: Path,
    handlers: dict,
    config=None,
) -> tuple[NaoServices, list[AgentRequest]]:
    """Build a NaoServices graph with all agents mapped to the test double."""
    cfg = config or load_config()
    adapter_class = make_adapter_class(handlers)
    adapter_classes = {name: adapter_class for name in cfg.agents}
    runs_dir = root / "runs"
    audit_dir = root / "audit"
    services = build_services(
        config=cfg,
        runs_dir=runs_dir,
        audit_dir=audit_dir,
        audit=AuditLogger(audit_dir),
        adapter_classes=adapter_classes,
    )
    return services, adapter_class.requests


def temp_dir() -> Path:
    path = Path(tempfile.mkdtemp(prefix="nao-test-"))
    return path


def trusted_internal_service() -> InternalProcessService:
    """A trusted internal process service for NAO-internal test subprocesses."""
    authority = LaunchAuthority()
    return InternalProcessService(authority, ProcessRunner(authority))


def run_git(cwd: Path, *args: str) -> None:
    result = trusted_internal_service().run(
        ["git", *args],
        cwd=str(cwd),
        timeout=60,
        purpose="test-git-setup",
    )
    if result.exit_code != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")


def init_repo(path: Path, filename: str = "app.py") -> Path:
    """Create a small git repository with one committed file."""
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q")
    run_git(path, "config", "user.email", "nao@test.local")
    run_git(path, "config", "user.name", "NAO Test")
    (path / filename).write_text("value = 1\n", encoding="utf-8")
    (path / "tests").mkdir(exist_ok=True)
    (path / "tests" / "test_app.py").write_text(
        "import unittest\n\n\nclass T(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    run_git(path, "add", "-A")
    run_git(path, "commit", "-q", "-m", "initial")
    return path


def write_in_workspace(request: AgentRequest, filename: str, text: str) -> None:
    """Side effect used by scripted implementers: write a file in the workspace."""
    assert request.workspace, "job has no workspace"
    target = Path(request.workspace) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def dummy_agent_config(name: str = "test", **overrides: Any) -> AgentConfig:
    data = {
        "enabled": True,
        "capabilities": ["implementation", "planning", "testing"],
        "interfaces": ["cli"],
        "base_args": ["--json"],
        "read_only_args": ["--plan"],
        "permission_mapping": {
            "write": {"allow": ["--auto-approve=true"]},
            "shell": {"allow": ["--auto-approve=true"]},
        },
        "discovery": {"commands": ["definitely-not-a-real-binary"], "paths": []},
    }
    data.update(overrides)
    return AgentConfig.from_dict(name, data)


def default_resource_config(**overrides: Any) -> ResourceConfig:
    values = {"max_concurrent_agents": 1}
    values.update(overrides)
    return ResourceConfig(**values)
