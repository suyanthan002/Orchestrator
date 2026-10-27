"""Externalized configuration (plan.md sections 23 and 24).

Agent configuration is data, not Python. Secrets are never stored inline:
values may reference environment variables with ``${ENV:NAME}`` and are
resolved only when an adapter needs them, never written to logs or audit
records.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .errors import ConfigError
from .models.agent_contract import AgentSpec
from .models.enums import ApprovalLevel, Capability, Permission, Role
from .paths import AGENTS_CONFIG, NAO_CONFIG, PROJECT_ROOT

_ENV_PATTERN = re.compile(r"\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_env(value: str) -> Optional[str]:
    """Resolve a single ``${ENV:NAME}`` reference, or return None if unset."""
    match = _ENV_PATTERN.fullmatch(value.strip())
    if not match:
        return None
    return os.environ.get(match.group(1))


def env_reference_name(value: str) -> Optional[str]:
    """Return the variable name for a ``${ENV:NAME}`` reference, else None.

    Unlike :func:`resolve_env`, this distinguishes "not a reference" from
    "a reference to an unset variable", which the environment policy needs.
    """
    if not isinstance(value, str):
        return None
    match = _ENV_PATTERN.fullmatch(value.strip())
    return match.group(1) if match else None


#: A key/field name matching any of these components is treated as
#: secret-bearing and its value is redacted. This is intentionally broad
#: (fail-closed): it covers SECRET, TOKEN, API_KEY, APIKEY, PASSWORD, PASSWD,
#: CLIENT_SECRET, PRIVATE_KEY, AUTH_TOKEN, ACCESS_TOKEN, GITHUB_TOKEN and
#: common casing/prefix/suffix variations such as AWS_SECRET_ACCESS_KEY.
#: Redaction is pattern-based and is therefore NOT an absolute security
#: boundary; see README.
_SECRET_KEY_PATTERN = re.compile(
    r"(?i)(api[_-]?key|apikey|secret|token|password|passwd|"
    r"private[_-]?key|credential|authorization|"
    r"auth[_-]?token|access[_-]?token)"
)


def redact_text(text: str) -> str:
    """Redact plausible secrets from a string before logging or persisting.

    Masks environment references, private keys, API keys (e.g. OpenAI sk-*,
    GitHub tokens ghp_*/github_pat_*, Slack xox*, Bearer tokens), and key-value
    assignments for secret/password/token/api_key fields while preserving useful
    diagnostic content.
    """
    if not text:
        return text
    # 1. Mask explicit env references
    text = _ENV_PATTERN.sub("[redacted-env-ref]", text)
    # 2. Mask PEM private keys
    text = re.sub(
        r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+ PRIVATE KEY-----",
        "[redacted-private-key]",
        text,
    )
    # 3. Known key/token patterns
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9_\-]{12,}\b", "[redacted-key]", text)
    text = re.sub(
        r"\b(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
        "[redacted-github-token]",
        text,
    )
    text = re.sub(
        r"\b(xox[baprs]-[A-Za-z0-9_\-]{10,})\b",
        "[redacted-slack-token]",
        text,
    )
    text = re.sub(
        r"(?i)\b(bearer\s+)[A-Za-z0-9_\-\.]{16,}\b",
        r"\1[redacted-token]",
        text,
    )
    # 4. Key-value pairs across lines or strings (prefix-tolerant so names like
    #    GITHUB_TOKEN and CLIENT_SECRET are also matched).
    text = re.sub(
        r"(?i)(api[_-]?key|apikey|secret|token|password|passwd|"
        r"auth[_-]?token|access[_-]?token|private[_-]?key|client[_-]?secret|"
        r"credential|authorization)\s*[:=]\s*([^\s\"',;]+)",
        r"\1=[redacted]",
        text,
    )
    return text


def redact_data(data: Any) -> Any:
    """Recursively redact secrets from arbitrary structured Python data."""
    if isinstance(data, str):
        return redact_text(data)
    elif isinstance(data, dict):
        result = {}
        for k, v in data.items():
            key_str = str(k)
            if _SECRET_KEY_PATTERN.search(key_str):
                result[key_str] = "[redacted]"
            else:
                result[key_str] = redact_data(v)
        return result
    elif isinstance(data, (list, tuple)):
        items = [redact_data(item) for item in data]
        return type(data)(items)
    elif isinstance(data, set):
        return {redact_data(item) for item in data}
    return data


# Keep `redact` alias pointing to `redact_text` for backward compatibility.
redact = redact_text


@dataclass
class AgentConfig:
    """Everything NAO knows about one agent, loaded from configuration."""

    name: str
    enabled: bool = True
    capabilities: tuple[Capability, ...] = ()
    interfaces: tuple[str, ...] = ()
    supports: dict[str, bool] = field(default_factory=dict)
    priority: int = 0
    timeout_seconds: int = 300
    mode: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    #: Native commands to try during discovery (``shutil.which`` names).
    discovery_commands: list[str] = field(default_factory=list)
    #: Explicit executable paths, checked before PATH discovery.
    discovery_paths: list[str] = field(default_factory=list)
    #: Extra arguments appended to every invocation.
    extra_args: list[str] = field(default_factory=list)
    #: NAO permission -> native permission control translation.
    permission_mapping: dict[str, Any] = field(default_factory=dict)
    #: Adapter-specific, non-shell options.
    raw: dict[str, Any] = field(default_factory=dict)

    def spec(self) -> AgentSpec:
        return AgentSpec(
            name=self.name,
            enabled=self.enabled,
            capabilities=self.capabilities,
            interfaces=self.interfaces,
            supports=dict(self.supports),
            priority=self.priority,
        )

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "AgentConfig":
        capabilities: list[Capability] = []
        for raw in data.get("capabilities", []):
            try:
                capabilities.append(Capability(raw))
            except ValueError as exc:
                raise ConfigError(
                    f"agent '{name}' declares unknown capability '{raw}'"
                ) from exc
        discovery = data.get("discovery", {})
        return cls(
            name=name,
            enabled=bool(data.get("enabled", True)),
            capabilities=tuple(capabilities),
            interfaces=tuple(data.get("interfaces", [])),
            supports=dict(data.get("supports", {})),
            priority=int(data.get("priority", 0)),
            timeout_seconds=int(data.get("timeout_seconds", 300)),
            mode=data.get("mode"),
            model=data.get("model"),
            provider=data.get("provider"),
            discovery_commands=list(discovery.get("commands", [])),
            discovery_paths=list(discovery.get("paths", [])),
            extra_args=list(data.get("extra_args", [])),
            permission_mapping=dict(data.get("permission_mapping", {})),
            raw=dict(data),
        )


@dataclass
class ResourceConfig:
    max_concurrent_agents: int = 1
    #: If available RAM drops below this percentage, NAO waits.
    min_available_ram_percent: float = 5.0
    queue_timeout_seconds: int = 1800
    poll_interval_seconds: float = 0.5


@dataclass
class NaoConfig:
    """Top-level NAO configuration."""

    agents: dict[str, AgentConfig] = field(default_factory=dict)
    resources: ResourceConfig = field(default_factory=ResourceConfig)
    #: role -> ordered agent preference list.
    routing: dict[Role, list[str]] = field(default_factory=dict)
    #: permission -> approval level override.
    approval_levels: dict[Permission, ApprovalLevel] = field(default_factory=dict)
    #: repository NAO is allowed to operate on, if not given per task.
    default_repo_path: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def agent(self, name: str) -> AgentConfig:
        try:
            return self.agents[name]
        except KeyError as exc:
            raise ConfigError(f"unknown agent '{name}'") from exc

    def enabled_agents(self) -> list[AgentConfig]:
        return [a for a in self.agents.values() if a.enabled]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"configuration file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc


def load_config(
    agents_path: Path = AGENTS_CONFIG,
    nao_path: Path = NAO_CONFIG,
) -> NaoConfig:
    """Load and validate the full NAO configuration."""
    agents_raw = _load_json(agents_path)
    nao_raw = _load_json(nao_path) if nao_path.exists() else {}

    agents_data = agents_raw.get("agents")
    if not isinstance(agents_data, dict) or not agents_data:
        raise ConfigError("agents.json must contain a non-empty 'agents' object")

    agents: dict[str, AgentConfig] = {}
    for name, data in agents_data.items():
        if not isinstance(data, dict):
            raise ConfigError(f"agent '{name}' config must be an object")
        agents[name] = AgentConfig.from_dict(name, data)

    resources_raw = nao_raw.get("resources", {})
    resources = ResourceConfig(
        max_concurrent_agents=int(resources_raw.get("max_concurrent_agents", 1)),
        min_available_ram_percent=float(
            resources_raw.get("min_available_ram_percent", 5.0)
        ),
        queue_timeout_seconds=int(resources_raw.get("queue_timeout_seconds", 1800)),
        poll_interval_seconds=float(
            resources_raw.get("poll_interval_seconds", 0.5)
        ),
    )
    if resources.max_concurrent_agents < 1:
        raise ConfigError("max_concurrent_agents must be >= 1")

    routing: dict[Role, list[str]] = {}
    for role_name, agent_names in nao_raw.get("routing", {}).items():
        try:
            role = Role(role_name)
        except ValueError as exc:
            raise ConfigError(f"routing declares unknown role '{role_name}'") from exc
        for agent_name in agent_names:
            if agent_name not in agents:
                raise ConfigError(
                    f"routing for role '{role_name}' references unknown agent "
                    f"'{agent_name}'"
                )
        routing[role] = list(agent_names)

    approval_levels: dict[Permission, ApprovalLevel] = {}
    for perm_name, level in nao_raw.get("approval_levels", {}).items():
        try:
            permission = Permission(perm_name)
        except ValueError as exc:
            raise ConfigError(
                f"approval_levels declares unknown permission '{perm_name}'"
            ) from exc
        try:
            approval_levels[permission] = ApprovalLevel(int(level))
        except ValueError as exc:
            raise ConfigError(
                f"invalid approval level {level!r} for permission '{perm_name}'"
            ) from exc

    default_repo = nao_raw.get("default_repo_path") or str(PROJECT_ROOT)

    return NaoConfig(
        agents=agents,
        resources=resources,
        routing=routing,
        approval_levels=approval_levels,
        default_repo_path=default_repo,
        raw=nao_raw,
    )
