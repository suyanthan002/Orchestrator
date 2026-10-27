"""Agent adapters.

Every adapter implements the NAO-facing :class:`~nao.adapters.base.AgentAdapter`
contract and hides all agent-specific interface details. The core never builds
an agent's shell command.
"""

from __future__ import annotations

from .base import AgentAdapter, Availability, BaseCliAdapter
from .aider import AiderAdapter
from .antigravity import AntigravityAdapter
from .cline import ClineAdapter
from .copilot import CopilotAdapter
from .cursor import CursorAdapter
from .goose import GooseAdapter
from .kilo import KiloAdapter

#: Registry of adapter classes keyed by the agent name used in configuration.
ADAPTER_CLASSES: dict[str, type[BaseCliAdapter]] = {
    "cline": ClineAdapter,
    "kilo": KiloAdapter,
    "goose": GooseAdapter,
    "aider": AiderAdapter,
    "copilot": CopilotAdapter,
    "cursor": CursorAdapter,
    "antigravity": AntigravityAdapter,
}

__all__ = [
    "AgentAdapter",
    "Availability",
    "BaseCliAdapter",
    "ADAPTER_CLASSES",
    "AiderAdapter",
    "AntigravityAdapter",
    "ClineAdapter",
    "CopilotAdapter",
    "CursorAdapter",
    "GooseAdapter",
    "KiloAdapter",
]
