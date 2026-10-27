"""Deliberate child-process environment construction (plan.md section 24 / F-06).

NAO never hands a child process the full host environment. Variables are
inherited from a small, explicit allowlist of variables required to launch
programs on Windows/POSIX, and any host variable whose *name* looks
secret-bearing is stripped.

This module is intentionally dependency-light: both the modern
``nao.process.ProcessRunner`` (used for agents, Git and tests) and the legacy
orchestrator runner import it, so there is exactly one environment policy.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from .config import env_reference_name

#: Host environment variables that may be inherited by a child process.
#: Only what is genuinely required to locate and launch an executable. Nothing
#: here is secret-bearing.
BASE_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        # OS / launcher plumbing
        "PATH", "Path", "PATHEXT",
        "SYSTEMROOT", "SystemRoot",
        "SYSTEMDRIVE", "SystemDrive",
        "WINDIR", "windir",
        "COMSPEC", "ComSpec",
        "TEMP", "TMP", "TMPDIR",
        "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME",
        "APPDATA", "LOCALAPPDATA",
        "PROGRAMFILES", "ProgramFiles",
        "PROGRAMFILES(X86)", "ProgramFiles(x86)",
        "PROGRAMW6432", "ProgramW6432",
        "COMMONPROGRAMFILES", "CommonProgramFiles",
        "PROGRAMDATA", "ProgramData",
        "USERNAME", "USERDOMAIN", "COMPUTERNAME",
        "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER",
        # Locale only. PYTHONPATH / NODE_PATH are deliberately NOT inherited:
        # they alter module resolution in the child and are not required to
        # launch a program (F-06).
        "LANG", "LC_ALL", "TERM",
    }
)

#: Case-insensitive view of the allowlist (Windows environment names are
#: case-insensitive; see PROTECTED_ENV_OVERRIDES).
_BASE_ENV_ALLOWLIST_UPPER: frozenset[str] = frozenset(
    name.upper() for name in BASE_ENV_ALLOWLIST
)

#: A variable whose name matches this is considered secret-bearing and is
#: never inherited (or accepted from a caller) unless its value is an explicit
#: ``${ENV:NAME}`` reference, which the policy can audit and resolve.
_SECRET_ENV_KEY_PATTERN = re.compile(
    r"(?i)(KEY|SECRET|TOKEN|PASSWORD|PASSWD|AUTH|CREDENTIAL)"
)

#: Launcher/runtime-critical variables a caller may never override. Overriding
#: these could hijack which executables or libraries a child loads, so the
#: baseline host value wins and the caller's value is dropped. Only the
#: sanitized baseline (and NAO itself) may set these.
PROTECTED_ENV_OVERRIDES: frozenset[str] = frozenset(
    {
        "PATH", "PATHEXT",
        "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE",
        "NODE_PATH", "NODE_OPTIONS",
        "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
        "COMSPEC",
        "SYSTEMROOT", "SYSTEMDRIVE",
        "WINDIR",
    }
)

#: Case-insensitive view of the protected set. Environment names are compared
#: case-insensitively so that ``PATH``, ``path``, ``Path`` and ``pAtH`` are all
#: protected equivalently (F-06), which is how Windows treats them.
_PROTECTED_ENV_UPPER: frozenset[str] = frozenset(
    name.upper() for name in PROTECTED_ENV_OVERRIDES
)


def is_protected_env_name(name: str) -> bool:
    """True when ``name`` is a launcher-critical variable a caller may not set."""
    return name.upper() in _PROTECTED_ENV_UPPER


def is_secret_env_name(name: str) -> bool:
    """True when ``name`` matches secret-bearing environment naming patterns."""
    return bool(_SECRET_ENV_KEY_PATTERN.search(name))


def build_sanitized_environment(
    custom_env: Optional[dict[str, str]] = None,
    allowlist: frozenset[str] = BASE_ENV_ALLOWLIST,
) -> dict[str, str]:
    """Construct a deliberate, isolated child process environment.

    This is the ONE environment policy: every NAO-controlled subprocess
    (agent, Git, tests, discovery, legacy runner, and any custom caller) passes
    through it. The documented merge policy is:

    1. Start from the allowlisted host baseline; secret-looking host names are
       never inherited.
    2. For each caller-supplied variable:
       * a *protected* launcher variable (``PATH``, ``PYTHONPATH``, ...) is
         ignored - the sanitized baseline wins and the caller cannot hijack it;
       * a value written as an explicit ``${ENV:NAME}`` reference is resolved
         from the host ONLY IF the referenced variable is neither secret-bearing
         nor protected (F-06); secret or protected references and references to
         an unset variable are safely dropped;
       * a variable whose *name* looks secret-bearing is rejected (dropped), so
         a caller cannot inject an API key, token or password;
       * any other variable is accepted as-is.

    This function never claims OS-level sandboxing; it enforces an environment
    allowlist only.
    """
    allowlist_upper = frozenset(name.upper() for name in allowlist)
    env: dict[str, str] = {}
    for key, val in os.environ.items():
        if key.upper() in allowlist_upper and not is_secret_env_name(key):
            env[key] = val

    if custom_env:
        for key, value in custom_env.items():
            if is_protected_env_name(key):
                # The policy forbids caller overrides of launcher-critical paths,
                # regardless of the caller's casing (PATH/path/Path/pAtH).
                continue
            if is_secret_env_name(key):
                # Raw inline secret: the policy rejects it.
                continue
            reference = env_reference_name(value)
            if reference is not None:
                # F-06: The referenced host variable must pass the same security
                # classification. Secret or protected host variables must not be
                # resolvable into child environments.
                if is_secret_env_name(reference) or is_protected_env_name(reference):
                    continue
                # Case-insensitive lookup across host environment
                resolved = os.environ.get(reference)
                if resolved is None:
                    ref_upper = reference.upper()
                    for k, v in os.environ.items():
                        if k.upper() == ref_upper:
                            resolved = v
                            break
                if resolved is None:
                    continue
                env[key] = resolved
                continue
            env[key] = value

    return env
