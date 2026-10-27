"""Agent Registry (plan.md sections 2, 17 and 24).

The registry is NAO's single source of truth about which agents exist, what
they can do, and whether they are currently usable. All agent construction is
centralized here; the core never imports a concrete adapter directly.
"""

from __future__ import annotations

from typing import Optional

from .adapters import ADAPTER_CLASSES, AgentAdapter, Availability
from .adapters.base import BaseCliAdapter
from .config import NaoConfig
from .errors import ConfigError
from .models.agent_contract import AgentSpec
from .models.enums import Capability
from .process import ProcessRunner


class AgentRegistry:
    """Holds configured agents and lazily builds their adapters."""

    def __init__(
        self,
        config: NaoConfig,
        runner: ProcessRunner,
        adapter_classes: Optional[dict[str, type]] = None,
    ) -> None:
        self.config = config
        self.runner = runner
        # Adapter classes are injectable so tests can substitute test doubles
        # without touching production code paths.
        self.adapter_classes = dict(adapter_classes or ADAPTER_CLASSES)
        self._adapters: dict[str, AgentAdapter] = {}

    # -- lookup ------------------------------------------------------------
    def names(self) -> list[str]:
        return list(self.config.agents)

    def adapter(self, name: str) -> AgentAdapter:
        if name not in self._adapters:
            if name not in self.config.agents:
                raise ConfigError(f"unknown agent '{name}'")
            # Fall back to the generic CLI adapter so a newly configured agent
            # works before a bespoke adapter exists.
            adapter_cls = self.adapter_classes.get(name, BaseCliAdapter)
            self._adapters[name] = adapter_cls(self.config.agent(name), self.runner)
        return self._adapters[name]

    def spec(self, name: str) -> AgentSpec:
        return self.config.agent(name).spec()

    def specs(self) -> list[AgentSpec]:
        return [cfg.spec() for cfg in self.config.agents.values()]

    # -- availability ------------------------------------------------------
    def availability(self, name: str) -> Availability:
        return self.adapter(name).availability()

    def is_available(self, name: str) -> bool:
        return self.availability(name).available

    def available_names(self) -> list[str]:
        return [name for name in self.names() if self.is_available(name)]

    def enabled_names(self) -> list[str]:
        return [cfg.name for cfg in self.config.enabled_agents()]

    # -- capability queries ------------------------------------------------
    def with_capabilities(
        self,
        required: "set[Capability] | list[Capability]",
        available_only: bool = True,
    ) -> list[str]:
        result = []
        for cfg in self.config.agents.values():
            if not cfg.enabled:
                continue
            if not cfg.spec().has_capabilities(required):
                continue
            if available_only and not self.is_available(cfg.name):
                continue
            result.append(cfg.name)
        return result

    # -- diagnostics -------------------------------------------------------
    def doctor(self) -> list[dict]:
        """Report per-agent availability for ``agent doctor``."""
        report = []
        for cfg in self.config.agents.values():
            availability = self.availability(cfg.name)
            report.append(
                {
                    "agent": cfg.name,
                    "enabled": cfg.enabled,
                    "available": availability.available,
                    "reason": availability.reason,
                    "executable": (
                        availability.executable.describe()
                        if availability.executable
                        else None
                    ),
                    "interfaces": list(cfg.interfaces),
                    "capabilities": [c.value for c in cfg.capabilities],
                }
            )
        return report

    def resolve_agent(self, name: Optional[str]) -> Optional[str]:
        """Return a usable agent name or None."""
        if name is None:
            return None
        return name if self.is_available(name) else None
