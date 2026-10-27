"""Agent executable discovery.

NAO must find each agent's native interface without relying on one hard-coded
installation path. Discovery is layered and deterministic:

1. an explicit environment override (``NAO_<AGENT>_EXECUTABLE``)
2. configured ``discovery.paths`` (existing files)
3. ``shutil.which`` for each configured command name
4. a Node-script fallback (locate ``node`` plus a configured global entry)

The Node fallback exists because npm-installed CLIs on Windows are commonly
``.cmd`` shims that fail when invoked directly by a non-shell launcher; going
through ``node`` and the real entry script is the robust path.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import AgentConfig
from .env import build_sanitized_environment


@dataclass(frozen=True)
class Executable:
    """A resolved launch prefix (argv[0:]) for an agent."""

    argv: tuple[str, ...]
    source: str

    def describe(self) -> str:
        return f"{' '.join(self.argv)} (via {self.source})"


def _npm_global_root() -> Optional[Path]:
    """Locate the npm global node_modules root, cached per-process."""
    cached = getattr(_npm_global_root, "_cache", None)
    if cached is not None:
        return cached

    root: Optional[Path] = None
    env_root = os.environ.get("NAO_NPM_GLOBAL_ROOT")
    if env_root and Path(env_root).is_dir():
        root = Path(env_root)
    else:
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if npm:
            try:
                completed = subprocess.run(
                    [npm, "root", "-g"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    shell=False,
                    # F-06: discovery is a NAO subprocess too; do not hand npm
                    # the host's secret-bearing environment.
                    env=build_sanitized_environment(),
                )
                if completed.returncode == 0 and completed.stdout.strip():
                    candidate = Path(completed.stdout.strip().splitlines()[-1])
                    if candidate.is_dir():
                        root = candidate
            except (OSError, subprocess.SubprocessError):
                root = None

    _npm_global_root._cache = root  # type: ignore[attr-defined]
    return root


def _env_override(agent_name: str) -> Optional[str]:
    env_name = f"NAO_{agent_name.upper().replace('-', '_')}_EXECUTABLE"
    value = os.environ.get(env_name)
    return value.strip() if value and value.strip() else None


def resolve_executable(config: AgentConfig) -> Optional[Executable]:
    """Resolve the launch argv for an agent, or None when undiscoverable."""
    override = _env_override(config.name)
    if override:
        path = Path(override)
        if path.exists():
            return Executable((str(path),), "env override")

    for candidate in config.discovery_paths:
        if Path(candidate).exists():
            return Executable((candidate,), "configured path")

    for command in config.discovery_commands:
        found = shutil.which(command)
        if not found:
            continue
        if not found.lower().endswith((".cmd", ".bat")):
            return Executable((found,), "PATH")
        # A Windows batch shim cannot be launched directly with shell=False.
        # Prefer the real Node entry script it wraps.
        node_script = (
            _resolve_node_entry(config)
            or _resolve_node_entry_from_shim(found)
        )
        if node_script:
            return node_script
        comspec = os.environ.get("COMSPEC")
        if comspec and Path(comspec).exists():
            return Executable((comspec, "/c", found), "cmd shim")
        return Executable((found,), "PATH (shim, unlaunchable)")

    node_script = _resolve_node_entry(config)
    if node_script:
        return node_script

    return None


_QUOTED_TOKEN = re.compile(r'"([^"]+)"')


def _resolve_node_entry_from_shim(shim_path: str) -> Optional[Executable]:
    """Extract the underlying Node entry script from an npm ``.cmd`` shim.

    npm shims are small text files that reference the real entry script, which
    frequently has no file extension. We substitute the shim's own directory
    for ``%~dp0``/``%dp0%`` and take the first referenced file that actually
    exists and is not the node runtime itself.
    """
    node = shutil.which("node")
    if not node:
        return None
    try:
        text = Path(shim_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    shim_dir = Path(shim_path).resolve().parent
    for match in _QUOTED_TOKEN.finditer(text):
        raw = match.group(1).strip()
        if not raw:
            continue
        resolved = (
            raw.replace("%~dp0", str(shim_dir))
            .replace("%dp0%", str(shim_dir))
            .replace("\\", os.sep)
        )
        if "%" in resolved:
            continue
        candidate = Path(resolved)
        if not candidate.is_absolute():
            candidate = shim_dir / candidate
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() in (".exe", ".cmd", ".bat"):
            continue
        return Executable((node, str(candidate)), "node entry (shim)")
    return None


def _resolve_node_entry(config: AgentConfig) -> Optional[Executable]:
    entry = config.raw.get("discovery", {}).get("node_entry")
    if not entry:
        return None
    node = shutil.which("node")
    if not node:
        return None
    root = _npm_global_root()
    if not root:
        return None
    # node_entry is relative to the global node_modules root.
    relative = entry.replace("\\", "/")
    candidates = [
        root / relative,
        root / (relative + ".js"),
        root / (relative + ".cjs"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return Executable((node, str(candidate)), "node entry")
    return None


def clear_discovery_cache() -> None:
    """Clear the memoized npm global root (used by tests)."""
    if hasattr(_npm_global_root, "_cache"):
        delattr(_npm_global_root, "_cache")
