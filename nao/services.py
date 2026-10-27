"""Dependency injection container and bootstrap.

All major services are constructed once and injected rather than imported
globally. This keeps subsystems decoupled and makes them mockable in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .approval import ApprovalManager
from .artifacts import ArtifactManager
from .audit import AuditLogger
from .config import NaoConfig, load_config
from .context import ContextManager
from .git import GitManager
from .paths import AGENTS_CONFIG, AUDIT_DIR, NAO_CONFIG, PROJECT_ROOT, RUNS_DIR, ensure_runtime_dirs
from .launch import LaunchAuthority
from .policy import PermissionPolicy
from .process import InternalProcessService, ProcessRunner
from .registry import AgentRegistry
from .resource import ResourceManager
from .review_manager import ReviewManager
from .router import AgentRouter
from .sessions import SessionManager
from .test_manager import TestManager


@dataclass
class NaoServices:
    """The wired-up NAO subsystems."""

    config: NaoConfig
    runner: ProcessRunner
    audit: AuditLogger
    artifacts: ArtifactManager
    approvals: ApprovalManager
    policy: PermissionPolicy
    resources: ResourceManager
    sessions: SessionManager
    context: ContextManager
    tests: TestManager
    review: ReviewManager
    registry: AgentRegistry
    router: AgentRouter
    git_factory: Callable[[str], GitManager]
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)


def build_services(
    config: Optional[NaoConfig] = None,
    *,
    agents_path: Path = AGENTS_CONFIG,
    nao_path: Path = NAO_CONFIG,
    runs_dir: Path = RUNS_DIR,
    audit_dir: Path = AUDIT_DIR,
    audit: Optional[AuditLogger] = None,
    git_factory: Optional[Callable[[str], GitManager]] = None,
    adapter_classes: Optional[dict[str, type]] = None,
) -> NaoServices:
    """Construct the full service graph. All I/O is confinable for tests."""
    ensure_runtime_dirs()
    cfg = config or load_config(agents_path, nao_path)

    # One trusted launch authority minted by the NAO internal subsystem and
    # shared only with the components permitted to launch processes (F-01).
    # Adapters receive the runner, never the authority.
    authority = LaunchAuthority()
    runner = ProcessRunner(authority)
    internal = InternalProcessService(authority, runner)
    audit_logger = audit or AuditLogger(audit_dir)
    artifacts = ArtifactManager(runs_dir)
    approvals = ApprovalManager(artifacts, audit_logger)
    policy = PermissionPolicy(cfg, approvals, audit_logger, authority=authority)
    resources = ResourceManager(cfg.resources, audit_logger)
    sessions = SessionManager(runs_dir / "sessions.json")
    context = ContextManager(artifacts, project_root=PROJECT_ROOT)
    tests = TestManager(artifacts, audit_logger, runner, internal=internal)
    review = ReviewManager(artifacts, audit_logger)

    registry = AgentRegistry(cfg, runner, adapter_classes=adapter_classes)
    router = AgentRouter(registry, cfg)

    if git_factory is None:
        def _factory(repo_path: str) -> GitManager:
            return GitManager(
                repo_path, audit=audit_logger, runner=runner, internal=internal
            )

        git_factory = _factory

    return NaoServices(
        config=cfg,
        runner=runner,
        audit=audit_logger,
        artifacts=artifacts,
        approvals=approvals,
        policy=policy,
        resources=resources,
        sessions=sessions,
        context=context,
        tests=tests,
        review=review,
        registry=registry,
        router=router,
        git_factory=git_factory,
    )
