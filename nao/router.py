"""Agent Router (plan.md sections 3 and 17).

Routing is capability-based and configuration-driven. NAO never assumes a
"best agent": it applies the configured role preference order, then filters by
capabilities, availability and permission compatibility, and reports why each
candidate was rejected so the decision is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import NaoConfig
from .models.enums import Capability, Permission, Role
from .registry import AgentRegistry

#: Permission -> capability an agent must have to exercise it.
PERMISSION_CAPABILITY: dict[Permission, Capability] = {
    Permission.WRITE: Capability.FILE_EDIT,
    Permission.DELETE: Capability.FILE_EDIT,
    Permission.SHELL: Capability.SHELL,
    Permission.PACKAGE_INSTALL: Capability.SHELL,
    Permission.MIGRATION: Capability.SHELL,
}


@dataclass
class RoutingDecision:
    agent: str
    role: Role
    reason: str
    candidates: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "role": self.role.value,
            "reason": self.reason,
            "candidates": self.candidates,
            "rejected": [
                {"agent": name, "reason": reason} for name, reason in self.rejected
            ],
        }


class RoutingError(Exception):
    """Raised when no agent can satisfy a role under the current constraints."""

    def __init__(self, role: Role, rejected: list[tuple[str, str]]):
        self.role = role
        self.rejected = rejected
        detail = "; ".join(f"{n}: {r}" for n, r in rejected) or "no agents configured"
        super().__init__(f"no eligible agent for role '{role.value}' ({detail})")

    def is_availability_only(self) -> bool:
        """True when every rejection is purely about availability, not policy.

        A task with no installed/authenticated agent is *blocked*; a task whose
        candidates were rejected for permission/capability/exclusion reasons
        needs a human decision (plan.md sections 20 and 22).
        """
        markers = (
            "UNSUPPORTED_PERMISSION",
            "missing capabilities",
            "cannot exercise permissions",
            "excluded",
            "disabled",
            "unknown agent",
        )
        return not any(
            marker in reason for _, reason in self.rejected for marker in markers
        )


class AgentRouter:
    def __init__(self, registry: AgentRegistry, config: NaoConfig) -> None:
        self.registry = registry
        self.config = config

    def role_order(self, role: Role) -> list[str]:
        """Configured preference order, falling back to all enabled agents."""
        configured = self.config.routing.get(role)
        if configured:
            return list(configured)
        enabled = self.registry.enabled_names()
        enabled.sort(key=lambda n: self.config.agent(n).priority, reverse=True)
        return enabled

    def candidates(
        self,
        role: Role,
        required_capabilities: "set[Capability] | list[Capability]" = (),
        granted_permissions: "set[Permission] | list[Permission]" = (),
        exclude: "set[str] | list[str]" = (),
        require_available: bool = True,
        overrides: "dict[str, str] | None" = None,  # noqa: F821
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """Return (ordered viable agents, rejections)."""
        required = set(required_capabilities)
        granted = set(granted_permissions)
        excluded = set(exclude)

        order = list(self.role_order(role))
        if overrides and role.value in overrides:
            preferred = overrides[role.value]
            order = [preferred] + [n for n in order if n != preferred]

        viable: list[str] = []
        rejected: list[tuple[str, str]] = []

        for name in order:
            if name in excluded:
                rejected.append((name, "excluded (previous failure)"))
                continue
            if name not in self.config.agents:
                rejected.append((name, "unknown agent"))
                continue
            cfg = self.config.agent(name)
            if not cfg.enabled:
                rejected.append((name, "disabled"))
                continue

            spec = cfg.spec()
            missing = sorted(c.value for c in required if not spec.has_capability(c))
            if missing:
                rejected.append((name, f"missing capabilities: {', '.join(missing)}"))
                continue

            incompatible = [
                p.value
                for p in granted
                if p in PERMISSION_CAPABILITY
                and not spec.has_capability(PERMISSION_CAPABILITY[p])
            ]
            if incompatible:
                rejected.append(
                    (name, f"cannot exercise permissions: {', '.join(incompatible)}")
                )
                continue

            adapter = self.registry.adapter(name)
            valid, reason = adapter.validate_permissions(tuple(granted))
            if not valid:
                rejected.append((name, reason))
                continue

            if require_available and not self.registry.is_available(name):
                rejected.append(
                    (name, self.registry.availability(name).reason)
                )
                continue

            viable.append(name)

        return viable, rejected

    def select(
        self,
        role: Role,
        required_capabilities: "set[Capability] | list[Capability]" = (),
        granted_permissions: "set[Permission] | list[Permission]" = (),
        exclude: "set[str] | list[str]" = (),
        require_available: bool = True,
        overrides: "dict[str, str] | None" = None,  # noqa: F821
    ) -> RoutingDecision:
        viable, rejected = self.candidates(
            role=role,
            required_capabilities=required_capabilities,
            granted_permissions=granted_permissions,
            exclude=exclude,
            require_available=require_available,
            overrides=overrides,
        )
        if not viable:
            raise RoutingError(role, rejected)

        chosen = viable[0]
        if overrides and role.value in overrides and overrides[role.value] == chosen:
            reason = "task routing override"
        else:
            index = self.role_order(role).index(chosen)
            reason = f"configured preference #{index + 1} for role '{role.value}'"

        return RoutingDecision(
            agent=chosen,
            role=role,
            reason=reason,
            candidates=viable,
            rejected=rejected,
        )
