"""Permission / Policy Engine (plan.md sections 8 and 20).

NAO never relies on an agent's own auto-approval switch as the security
boundary. It has its own permission vocabulary, its own approval levels, and it
translates the resulting decision into agent-native controls inside adapters.

Permission status is one of:

* ``allowed``            - granted automatically (level 0) or by task grant
* ``requires_approval``  - allowed by task grant but blocked on human approval
* ``denied``             - not granted by the task, or explicitly denied
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .approval import ApprovalManager
from .audit import AuditLogger
from .config import NaoConfig
from .launch import AgentLaunchAuthorization, LaunchAuthority
from .models.enums import ApprovalLevel, Permission
from .models.events import EventType
from .permissions import NativeArgumentContract, contract_from_agent_config, default_contract

#: Default approval level per permission (plan.md sections 8 and 20).
DEFAULT_LEVELS: dict[Permission, ApprovalLevel] = {
    Permission.READ: ApprovalLevel.AUTOMATIC,
    Permission.SEARCH: ApprovalLevel.AUTOMATIC,
    Permission.GIT_READ: ApprovalLevel.AUTOMATIC,
    Permission.WRITE: ApprovalLevel.CONTROLLED,
    Permission.SHELL: ApprovalLevel.CONTROLLED,
    Permission.PACKAGE_INSTALL: ApprovalLevel.CONTROLLED,
    Permission.MIGRATION: ApprovalLevel.CONTROLLED,
    Permission.DELETE: ApprovalLevel.HUMAN_APPROVAL,
    Permission.GIT_COMMIT: ApprovalLevel.HUMAN_APPROVAL,
    Permission.GIT_MERGE: ApprovalLevel.HUMAN_APPROVAL,
    Permission.NETWORK: ApprovalLevel.HUMAN_APPROVAL,
    Permission.SECRET_ACCESS: ApprovalLevel.HUMAN_APPROVAL,
    Permission.GIT_PUSH: ApprovalLevel.ALWAYS_HUMAN,
    Permission.DEPLOYMENT: ApprovalLevel.ALWAYS_HUMAN,
}


@dataclass(frozen=True)
class PolicyDecision:
    permission: Permission
    allowed: bool
    requires_approval: bool
    level: ApprovalLevel
    reason: str

    @property
    def denied(self) -> bool:
        return not self.allowed and not self.requires_approval

    def to_dict(self) -> dict:
        return {
            "permission": self.permission.value,
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "level": int(self.level),
            "reason": self.reason,
        }


class PermissionPolicy:
    def __init__(
        self,
        config: NaoConfig,
        approvals: ApprovalManager,
        audit: AuditLogger,
        authority: Optional[LaunchAuthority] = None,
    ) -> None:
        self.config = config
        self.approvals = approvals
        self.audit = audit
        #: The trusted authority that mints policy-issued agent launch grants.
        self.authority = authority or LaunchAuthority()
        self._levels = dict(DEFAULT_LEVELS)
        self._levels.update(config.approval_levels)

    # -- launch grants (F-01) ---------------------------------------------
    def launch_contract(self, agent: str) -> NativeArgumentContract:
        """The *trusted* native-argument contract for ``agent``.

        Derived from NAO's own configuration (which the anchor workflow owns),
        never from the adapter request being authorized, so a task- or
        adapter-supplied contract cannot weaken final authorization.
        """
        try:
            return contract_from_agent_config(agent, self.config.agent(agent))
        except Exception:
            return default_contract(agent)

    def issue_agent_grant(
        self,
        *,
        agent: str,
        permissions: Iterable[Permission],
        task_id: Optional[str] = None,
        job_id: Optional[str] = None,
        role: Optional[object] = None,
    ) -> AgentLaunchAuthorization:
        """Issue a policy-issued grant for an already-authorized permission set.

        Callers must have obtained ``permissions`` from :meth:`authorize_all`:
        the grant binds exactly those permissions together with NAO's trusted
        native-argument contract, so an adapter can neither widen them nor swap
        the contract at the subprocess boundary.
        """
        return self.authority.issue_agent_grant(
            agent=agent,
            permissions=frozenset(permissions),
            contract=self.launch_contract(agent),
            task_id=task_id,
            job_id=job_id,
            role=role,
        )

    def level(self, permission: Permission) -> ApprovalLevel:
        return self._levels.get(permission, ApprovalLevel.HUMAN_APPROVAL)

    def evaluate(
        self,
        permission: Permission,
        granted: Iterable[Permission],
        task_id: Optional[str] = None,
        job_id: Optional[str] = None,
        audit: bool = True,
    ) -> PolicyDecision:
        granted_set = set(granted)
        level = self.level(permission)
        subject = f"permission:{permission.value}"

        if level is ApprovalLevel.AUTOMATIC:
            return self._finish(
                PolicyDecision(
                    permission,
                    allowed=True,
                    requires_approval=False,
                    level=level,
                    reason="level 0: automatic",
                ),
                task_id,
                job_id,
                audit,
            )

        if level is ApprovalLevel.CONTROLLED:
            # Level 1 is gated on an explicit *task* permission.
            if permission not in granted_set:
                return self._finish(
                    PolicyDecision(
                        permission,
                        allowed=False,
                        requires_approval=False,
                        level=level,
                        reason="not granted by the task",
                    ),
                    task_id,
                    job_id,
                    audit,
                )
            return self._finish(
                PolicyDecision(
                    permission,
                    allowed=True,
                    requires_approval=False,
                    level=level,
                    reason="level 1: granted by task permission",
                ),
                task_id,
                job_id,
                audit,
            )

        # Level 2 and 3 require an explicit, recorded human approval.
        if task_id and self.approvals.is_granted(task_id, subject, job_id):
            return self._finish(
                PolicyDecision(
                    permission,
                    allowed=True,
                    requires_approval=False,
                    level=level,
                    reason=f"level {int(level)}: approved by human",
                ),
                task_id,
                job_id,
                audit,
            )
        if task_id and self.approvals.is_denied(task_id, subject):
            return self._finish(
                PolicyDecision(
                    permission,
                    allowed=False,
                    requires_approval=False,
                    level=level,
                    reason="explicitly denied by human",
                ),
                task_id,
                job_id,
                audit,
            )
        return self._finish(
            PolicyDecision(
                permission,
                allowed=False,
                requires_approval=True,
                level=level,
                reason=f"level {int(level)}: requires human approval",
            ),
            task_id,
            job_id,
            audit,
        )

    def _finish(
        self,
        decision: PolicyDecision,
        task_id: Optional[str],
        job_id: Optional[str],
        audit: bool,
    ) -> PolicyDecision:
        if audit:
            if decision.allowed:
                event = EventType.PERMISSION_GRANTED
            elif decision.requires_approval:
                event = EventType.PERMISSION_REQUESTED
            else:
                event = EventType.PERMISSION_DENIED
            self.audit.record(
                event,
                task_id=task_id,
                job_id=job_id,
                status=decision.permission.value,
                message=decision.reason,
            )
        return decision

    def authorize_all(
        self,
        permissions: Iterable[Permission],
        granted: Iterable[Permission],
        task_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> tuple[list[Permission], list[PolicyDecision]]:
        """Return (effective permissions, blocking decisions)."""
        effective: list[Permission] = []
        blocking: list[PolicyDecision] = []
        for permission in permissions:
            decision = self.evaluate(permission, granted, task_id, job_id)
            if decision.allowed:
                effective.append(permission)
            else:
                blocking.append(decision)
        return effective, blocking
