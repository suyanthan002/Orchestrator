"""Authoritative native-argument permission analysis (plan.md section 8 / F-01).

NAO's permission model is authoritative. An adapter may translate an authorized
NAO permission into a native flag, but it must never introduce a native argument
that grants capability NAO did not authorize.

The single, shared boundary is :func:`analyze_native_arguments`. It evaluates an
adapter's emitted argv against an explicit, **declarative**
:class:`NativeArgumentContract`, which classifies every native argument as one
of:

* a fixed, harmless argument,
* a permission-bearing argument (and the NAO capabilities it grants),
* a data argument (whose value is free text, never parsed as a flag), or
* a forbidden/undeclared argument.

The analysis is deliberately conservative (fail-closed):

* A bare flag-like token that is **not declared** by the contract is rejected.
  "Unknown" is never treated as "safe".
* A *granting* flag whose granted capability set is not a subset of the
  authorized permissions is rejected.
* A *blanket* flag (``--auto-approve=true``, ``--yes``, ``--force``,
  ``--dangerously-skip-permissions`` ...) counts as granting **all** mutating
  capability, so it is only accepted when every capability it grants is
  authorized. It must be declared by the contract to be considered at all.

This module holds the contract and the analysis shared by:

* the modern adapters (``nao.adapters.base``) for every command they build, and
* the final subprocess authorization boundary (``nao.process.ProcessRunner``),
  and
* the legacy prototype path (``adapters/cline.py`` and ``orchestrator.runner``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Optional

from .errors import UnsupportedPermissionError
from .models.enums import Permission

#: What a blanket "approve/auto/force" switch is treated as granting.
#: Kept to the two capabilities that blanket switches actually bypass in the
#: supported agents (file edits and shell/tool execution).
BLANKET_MUTATING: frozenset[Permission] = frozenset(
    {Permission.WRITE, Permission.SHELL}
)


@dataclass(frozen=True)
class NativeFlagRule:
    """A native argument and the NAO permissions it effectively grants."""

    flag: str
    grants: frozenset[Permission]
    blanket: bool = False


@dataclass(frozen=True)
class NativeArgumentContract:
    """A declarative description of an adapter's native arguments.

    The contract is the *only* authority the analyzer consults. Anything that
    looks like a flag and is not classified here fails closed.
    """

    agent: str
    #: Exact flag tokens (or ``--flag=value`` tokens) that are proven harmless.
    harmless: frozenset[str] = frozenset()
    #: Flags that grant NAO capability, with the capability they grant.
    permission_bearing: tuple[NativeFlagRule, ...] = ()
    #: Flags whose following token is free-form data (never parsed as a flag).
    data_flags: frozenset[str] = frozenset()
    #: Exact tokens that are explicitly forbidden.
    forbidden: frozenset[str] = frozenset()
    #: Flags that take a separate value token in ``["--flag", "value"]`` form.
    #: (Data flags are always value-taking.)
    valued_flags: frozenset[str] = frozenset()

    def rule_for(self, flag: str) -> Optional[NativeFlagRule]:
        for rule in self.permission_bearing:
            if rule.flag == flag:
                return rule
        return None

    def with_extra(
        self,
        *,
        harmless: Iterable[str] = (),
        forbidden: Iterable[str] = (),
        data_flags: Iterable[str] = (),
        valued_flags: Iterable[str] = (),
    ) -> "NativeArgumentContract":
        return replace(
            self,
            harmless=self.harmless | frozenset(harmless),
            forbidden=self.forbidden | frozenset(forbidden),
            data_flags=self.data_flags | frozenset(data_flags),
            valued_flags=self.valued_flags | frozenset(valued_flags),
        )


#: Known native flags that grant (rather than merely restrict) capability.
#: Only flags declared here (or by an adapter's configuration) are considered
#: permission-bearing; every other flag-like token fails closed.
_GLOBAL_PERMISSION_RULES: tuple[NativeFlagRule, ...] = (
    NativeFlagRule("--auto-approve=true", BLANKET_MUTATING, blanket=True),
    NativeFlagRule("--auto-approve", BLANKET_MUTATING, blanket=True),
    NativeFlagRule("--dangerously-skip-permissions", BLANKET_MUTATING, blanket=True),
    NativeFlagRule(
        "--dangerously-bypass-approvals-and-sandbox", BLANKET_MUTATING, blanket=True
    ),
    NativeFlagRule("--yes", BLANKET_MUTATING, blanket=True),
    NativeFlagRule("-y", BLANKET_MUTATING, blanket=True),
    NativeFlagRule("--force", BLANKET_MUTATING, blanket=True),
    NativeFlagRule("--allow-tool=write", frozenset({Permission.WRITE})),
    NativeFlagRule("--allow-tool=edit", frozenset({Permission.WRITE})),
    NativeFlagRule("--allow-tool=shell", frozenset({Permission.SHELL})),
    NativeFlagRule("--allow-tool=execute", frozenset({Permission.SHELL})),
    NativeFlagRule("--allow-tool=network", frozenset({Permission.NETWORK})),
    NativeFlagRule(
        "--permission-mode=accept-edits", frozenset({Permission.WRITE})
    ),
    # Aider's ``--auto-commits`` enables automatic commits, i.e. it grants the
    # GIT_COMMIT capability. It must be declared as permission-bearing so it can
    # never be emitted as if it were a restrictive "deny" flag.
    NativeFlagRule("--auto-commits", frozenset({Permission.GIT_COMMIT})),
)

#: Flags that are known to be fixed and harmless (they only restrict capability
#: or select output format). An adapter may declare additional harmless flags
#: through configuration (``harmless_args``).
_GLOBAL_HARMLESS: frozenset[str] = frozenset(
    {
        "--json",
        "--plan",
        "--dry-run",
        "--no-stream",
        "--no-auto-commits",
        "--silent",
        "--no-session",
        "--no-auto-approve",
        "--mode=plan",
        "--permission-mode=plan",
        "--output-format=stream-json",
        "--deny-tool=write",
        "--deny-tool=edit",
        "--deny-tool=shell",
        "--deny-tool=execute",
        "--deny-tool=network",
    }
)

#: Flags whose following token is free-form data (a prompt, session id, ...).
_GLOBAL_DATA_FLAGS: frozenset[str] = frozenset(
    {
        "-p",
        "-t",
        "--message",
        "--message-file",
        "--session",
        "--session-id",
        "--conversation",
        "--conversation-id",
    }
)

#: Flags that take a separate value token but are not data flags (e.g. a
#: two-token permission flag such as ``--permission-mode accept-edits``).
_GLOBAL_VALUED_FLAGS: frozenset[str] = frozenset(
    {
        "--permission-mode",
        "--allow-tool",
        "--deny-tool",
        "--output-format",
        "--mode",
    }
)

_GLOBAL_FORBIDDEN: frozenset[str] = frozenset()


def default_contract(agent: str = "*") -> NativeArgumentContract:
    """The conservative base contract shared by every adapter."""
    return NativeArgumentContract(
        agent=agent,
        harmless=_GLOBAL_HARMLESS,
        permission_bearing=_GLOBAL_PERMISSION_RULES,
        data_flags=_GLOBAL_DATA_FLAGS,
        forbidden=_GLOBAL_FORBIDDEN,
        valued_flags=_GLOBAL_VALUED_FLAGS,
    )


def _combined_forms(tokens: Iterable[object]) -> list[str]:
    """Normalize a declared flag list into canonical ``--flag=value`` forms.

    Adapters declare some flags as two tokens (``["--permission-mode",
    "accept-edits"]``) and others as one (``"--allow-tool=write"``). Both must
    be captured as a single, comparable rule token.
    """
    items = [str(token) for token in tokens]
    forms: list[str] = []
    index = 0
    while index < len(items):
        current = items[index]
        if (
            current.startswith("--")
            and "=" not in current
            and index + 1 < len(items)
            and not items[index + 1].startswith("-")
        ):
            forms.append(f"{current}={items[index + 1]}")
            index += 2
            continue
        forms.append(current)
        index += 1
    return forms


def contract_from_agent_config(agent: str, config: object) -> NativeArgumentContract:
    """Build the authoritative contract for one configured agent.

    Starts from the conservative global contract and folds in the adapter's
    explicit configuration: extra harmless/forbidden/data/valued flags, the
    prompt/session flags, and the permission mapping's allow/deny tokens.
    """
    raw = getattr(config, "raw", {}) or {}
    contract = default_contract(agent)

    harmless = set(contract.harmless)
    forbidden = set(contract.forbidden)
    data_flags = set(contract.data_flags)
    valued = set(contract.valued_flags)

    harmless |= {str(a) for a in raw.get("harmless_args", []) or []}
    forbidden |= {str(a) for a in raw.get("forbidden_args", []) or []}
    data_flags |= {str(a) for a in raw.get("data_flags", []) or []}
    valued |= {str(a) for a in raw.get("valued_args", []) or []}

    for key in ("prompt_flag", "session_flag"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            data_flags.add(value)
            valued.add(value)

    rules: dict[str, NativeFlagRule] = {
        rule.flag: rule for rule in contract.permission_bearing
    }

    mapping = getattr(config, "permission_mapping", {}) or {}
    for permission_name, spec in mapping.items():
        try:
            permission = Permission(permission_name)
        except ValueError:
            continue
        if not isinstance(spec, dict):
            continue
        for token in _combined_forms(spec.get("allow", []) or []):
            # A flag that is already explicitly declared keeps its declared
            # grants; configuration mapping is a translation hint, not a way to
            # widen what a token grants. A newly declared token becomes its own
            # explicit rule.
            if token not in rules:
                rules[token] = NativeFlagRule(
                    token, frozenset({permission}), False
                )
            if "=" in token:
                valued.add(token.split("=", 1)[0])
        for token in _combined_forms(spec.get("deny", []) or []):
            # A deny flag only restricts capability, so it is harmless.
            harmless.add(token)
            if "=" in token:
                valued.add(token.split("=", 1)[0])

    return NativeArgumentContract(
        agent=agent,
        harmless=frozenset(harmless),
        permission_bearing=tuple(rules.values()),
        data_flags=frozenset(data_flags),
        forbidden=frozenset(forbidden),
        valued_flags=frozenset(valued),
    )


def _iter_flag_units(args: Iterable[object], contract: NativeArgumentContract):
    """Yield ``(flag_or_None, value_or_None)`` logical argument units.

    A two-token valued flag (``["--permission-mode", "plan"]``) is normalized
    to ``"--permission-mode=plan"`` so it can be compared against the contract.
    Positional tokens (file paths, the prompt when no flag is used) are yielded
    with ``None`` for the flag.
    """
    values = [str(arg) for arg in args]
    valued = contract.valued_flags | contract.data_flags
    index = 0
    while index < len(values):
        token = values[index]
        if not token.startswith("-") or token == "-":
            yield None, token
            index += 1
            continue
        if "=" in token:
            yield token, None
            index += 1
            continue
        if token in valued and index + 1 < len(values):
            yield f"{token}={values[index + 1]}", values[index + 1]
            index += 2
            continue
        yield token, None
        index += 1


def analyze_native_arguments(
    args: Iterable[object],
    granted: Iterable[Permission],
    contract: Optional[NativeArgumentContract] = None,
) -> Optional[str]:
    """Return a fail-closed rejection reason, or None when all args are safe.

    Every argument that looks like a flag must be declared by ``contract``,
    either as harmless, as data, or as permission-bearing. Anything else is
    rejected: "unknown" is never assumed safe.
    """
    active = contract or default_contract()
    granted_set = set(granted)
    for flag, _value in _iter_flag_units(args, active):
        if flag is None:
            continue  # positional data (prompt, path, ...)

        if flag in active.forbidden:
            return (
                f"UNSUPPORTED_PERMISSION: agent native arg '{flag}' is explicitly "
                f"forbidden by the adapter contract"
            )

        rule = active.rule_for(flag)
        if rule is not None:
            if not rule.grants <= granted_set:
                missing = sorted(p.value for p in rule.grants - granted_set)
                kind = "blanket auto-approval" if rule.blanket else "permission"
                return (
                    f"UNSUPPORTED_PERMISSION: agent native flag '{flag}' is a "
                    f"{kind} flag granting "
                    f"[{', '.join(sorted(p.value for p in rule.grants))}], which "
                    f"exceeds the authorized permissions "
                    f"[{', '.join(sorted(p.value for p in granted_set)) or 'none'}] "
                    f"(missing: {', '.join(missing)})"
                )
            continue

        base = flag.split("=", 1)[0]
        if flag in active.data_flags or base in active.data_flags:
            continue
        if flag in active.harmless:
            continue

        return (
            f"UNSUPPORTED_PERMISSION: agent native arg '{flag}' is not declared in "
            f"the adapter's native-argument contract and cannot be proven harmless; "
            f"failing closed"
        )
    return None


def assert_native_arguments_authorized(
    args: Iterable[object],
    granted: Iterable[Permission],
    agent: str,
    contract: Optional[NativeArgumentContract] = None,
) -> None:
    """Raise :class:`UnsupportedPermissionError` when an argument over-grants."""
    reason = analyze_native_arguments(args, granted, contract)
    if reason:
        raise UnsupportedPermissionError(agent, "native-argv", reason)


# -- backward-compatible aliases -------------------------------------------
def analyze_native_flags(
    args: Iterable[object], granted: Iterable[Permission]
) -> Optional[str]:
    """Analyze args against the default (conservative) contract."""
    return analyze_native_arguments(args, granted, default_contract())


def assert_native_flags_authorized(
    args: Iterable[object], granted: Iterable[Permission], agent: str
) -> None:
    """Raise when an argument over-grants, using the default contract."""
    assert_native_arguments_authorized(args, granted, agent, default_contract())
