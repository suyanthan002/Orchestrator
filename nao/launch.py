"""Trusted launch-authorization subsystem (F-01).

NAO distinguishes two very different trust domains:

* an **untrusted agent process** - the native CLI NAO spawns, whose argv is
  derived from a policy decision, and
* **trusted NAO Python adapter code** - in-process code that NAO itself loaded.

In-process adapters are trusted infrastructure. A malicious adapter implementation
requires process isolation to be contained.

``ProcessRunner`` enforces a *policy* boundary against the first: an adapter may
never hand it a capability (native argv) that the NAO policy did not authorize.
It is explicitly **not** a security boundary against the second. A third-party
adapter is trusted in-process code; if it calls ``subprocess.Popen`` directly no
in-process mechanism can stop it, and NAO does not claim otherwise (see README).

Authorizations are minted only by a :class:`LaunchAuthority` and stored in an
authority-owned internal registry. The :class:`~nao.process.ProcessRunner`
boundary accepts an authorization only when verified by a :class:`LaunchVerifier`
bound to the issuing authority and proving the authorization has not been forged
or mutated (e.g. via ``dataclasses.replace``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from uuid import uuid4

from .models.enums import Permission
from .permissions import NativeArgumentContract, default_contract


@dataclass(frozen=True)
class _RegisteredGrant:
    grant_id: str
    agent: str
    permissions: frozenset[Permission]
    contract: NativeArgumentContract
    task_id: Optional[str]
    job_id: Optional[str]
    role: Optional[object]
    authority_id: object


@dataclass(frozen=True)
class _RegisteredInternal:
    auth_id: str
    purpose: str
    authority_id: object


class LaunchVerifier:
    """Read-only verifier for process-launch authorizations.

    ProcessRunner holds only this verifier, not the LaunchAuthority itself, so
    adapter-visible runners possess zero grant-minting capability.
    """

    def __init__(
        self,
        authority_id: object,
        agent_grants: dict[str, _RegisteredGrant],
        internal_grants: dict[str, _RegisteredInternal],
    ) -> None:
        self._authority_id = authority_id
        self._agent_grants = agent_grants
        self._internal_grants = internal_grants

    def verify(self, authorization: object) -> bool:
        """True only for an authorization the paired authority issued and has not been mutated."""
        if isinstance(authorization, AgentLaunchAuthorization):
            grant_id = getattr(authorization, "_grant_id", None)
            if not grant_id or grant_id not in self._agent_grants:
                return False
            registered = self._agent_grants[grant_id]
            if registered.authority_id is not self._authority_id:
                return False
            if getattr(authorization, "_authority_id", None) is not self._authority_id:
                return False
            # Verify exact immutable field equality against the registered grant
            if authorization.agent != registered.agent:
                return False
            if authorization.permissions != registered.permissions:
                return False
            if authorization.contract != registered.contract:
                return False
            if authorization.task_id != registered.task_id:
                return False
            if authorization.job_id != registered.job_id:
                return False
            if authorization.role != registered.role:
                return False
            return True

        if isinstance(authorization, InternalLaunchAuthorization):
            auth_id = getattr(authorization, "_auth_id", None)
            if not auth_id or auth_id not in self._internal_grants:
                return False
            registered_internal = self._internal_grants[auth_id]
            if registered_internal.authority_id is not self._authority_id:
                return False
            if getattr(authorization, "_authority_id", None) is not self._authority_id:
                return False
            if authorization.purpose != registered_internal.purpose:
                return False
            return True

        return False


class LaunchAuthority:
    """The single trusted issuer of process-launch authorizations.

    Exactly one authority is created by the NAO bootstrap
    (:func:`nao.services.build_services`) and shared only with the trusted
    components that are permitted to launch processes:

    * the internal process service (NAO's own Git/test/discovery subprocesses),
    * the permission policy (policy-issued *agent* grants), and
    * the :class:`~nao.process.ProcessRunner` enforcement boundary (via :class:`LaunchVerifier`).

    Adapters are handed a ``ProcessRunner`` holding a ``LaunchVerifier``, never a
    ``LaunchAuthority``, so they cannot mint an authorization the real boundary will accept.
    """

    def __init__(self) -> None:
        self._authority_id: object = object()
        self._agent_grants: dict[str, _RegisteredGrant] = {}
        self._internal_grants: dict[str, _RegisteredInternal] = {}
        self.verifier: LaunchVerifier = LaunchVerifier(
            self._authority_id,
            self._agent_grants,
            self._internal_grants,
        )

    # -- issuance ----------------------------------------------------------
    def issue_internal(self, purpose: str = "internal") -> "InternalLaunchAuthorization":
        """Mint an authorization for a NAO-controlled, non-agent subprocess."""
        auth_id = uuid4().hex
        self._internal_grants[auth_id] = _RegisteredInternal(
            auth_id=auth_id,
            purpose=purpose,
            authority_id=self._authority_id,
        )
        return InternalLaunchAuthorization(
            purpose=purpose,
            _auth_id=auth_id,
            _authority_id=self._authority_id,
        )

    def issue_agent_grant(
        self,
        *,
        agent: str,
        permissions: Iterable[Permission],
        contract: Optional[NativeArgumentContract] = None,
        task_id: Optional[str] = None,
        job_id: Optional[str] = None,
        role: Optional[object] = None,
    ) -> "AgentLaunchAuthorization":
        """Mint a policy-issued grant for an agent subprocess.

        Only the permission policy calls this, and only after it has evaluated
        the requested permissions against the task's granted permissions.
        """
        grant_id = uuid4().hex
        trusted_contract = contract or default_contract(agent)
        perms = frozenset(permissions)
        self._agent_grants[grant_id] = _RegisteredGrant(
            grant_id=grant_id,
            agent=agent,
            permissions=perms,
            contract=trusted_contract,
            task_id=task_id,
            job_id=job_id,
            role=role,
            authority_id=self._authority_id,
        )
        return AgentLaunchAuthorization(
            agent=agent,
            permissions=perms,
            contract=trusted_contract,
            task_id=task_id,
            job_id=job_id,
            role=role,
            _grant_id=grant_id,
            _authority_id=self._authority_id,
        )

    # -- verification ------------------------------------------------------
    def verify(self, authorization: object) -> bool:
        """True only for an authorization this exact authority issued."""
        return self.verifier.verify(authorization)


@dataclass(frozen=True)
class InternalLaunchAuthorization:
    """Authorization for a NAO-controlled, non-agent subprocess.

    Used for Git, test and discovery subprocesses that NAO itself constructs and
    whose argv is never agent-controlled. It cannot be created directly; use
    :meth:`LaunchAuthority.issue_internal` (via the internal process service).
    """

    purpose: str = "internal"
    _auth_id: Optional[str] = field(default=None, repr=False, compare=False)
    _authority_id: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._auth_id is None or self._authority_id is None:
            raise TypeError(
                "launch authorizations are issued only by the NAO launch authority "
                "and cannot be constructed directly"
            )


@dataclass(frozen=True)
class AgentLaunchAuthorization:
    """A **policy-issued** grant to launch an agent subprocess.

    The final subprocess boundary re-validates the exact final argv against the
    grant's trusted :class:`NativeArgumentContract` and the permissions the
    policy authorized. An adapter cannot fabricate one, cannot widen the
    permission set, and cannot substitute the contract.
    """

    agent: str
    permissions: frozenset[Permission]
    contract: NativeArgumentContract
    task_id: Optional[str] = None
    job_id: Optional[str] = None
    role: Optional[object] = None
    _grant_id: Optional[str] = field(default=None, repr=False, compare=False)
    _authority_id: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._grant_id is None or self._authority_id is None:
            raise TypeError(
                "launch authorizations are issued only by the NAO launch authority "
                "and cannot be constructed directly"
            )
