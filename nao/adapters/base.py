"""The NAO Agent Contract: the adapter interface (plan.md section 15).

An adapter knows how to:

* report availability,
* describe capabilities,
* translate NAO permissions into native controls,
* build the native command,
* execute the process,
* normalize the native result into an :class:`AgentResponse`,
* classify errors,
* cancel when supported,
* preserve session identifiers when supported.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from ..config import AgentConfig
from ..discovery import Executable, resolve_executable
from ..env import BASE_ENV_ALLOWLIST, build_sanitized_environment  # noqa: F401
from ..errors import FailureClass, UnsupportedPermissionError, classify_text
from ..models.agent_contract import AgentRequest, AgentResponse, AgentSpec
from ..models.enums import Capability, JobStatus, Permission
from ..permissions import (
    NativeArgumentContract,
    analyze_native_arguments,
    assert_native_arguments_authorized,
    contract_from_agent_config,
)
from ..process import (
    ProcessResult,
    ProcessRunner,
)

#: Permissions that imply an agent may mutate its workspace.
_MUTATING_PERMISSIONS = frozenset(
    {
        Permission.WRITE,
        Permission.DELETE,
        Permission.SHELL,
        Permission.PACKAGE_INSTALL,
        Permission.MIGRATION,
    }
)


def format_agent_prompt(request: AgentRequest) -> str:
    """Format the complete prompt envelope delivering task instruction, context pack, and output contract (plan.md section 15 / F-02)."""
    parts: list[str] = []

    # 1. Task instruction
    parts.append(f"=== TASK INSTRUCTION ===\n{request.prompt.strip()}")

    # 2. Workspace (if specified)
    if request.workspace:
        parts.append(f"=== WORKSPACE ===\n{request.workspace}")

    # 3. Permissions (if specified)
    if request.permissions:
        perms_str = ", ".join(p.value if hasattr(p, "value") else str(p) for p in request.permissions)
        parts.append(f"=== PERMISSIONS ===\nGranted permissions: {perms_str}")

    # 4. Context Pack (F-02)
    if request.context and request.context.strip():
        parts.append(f"=== CONTEXT PACK ===\n{request.context.strip()}")

    # 5. Expected Output Contract / Schema (F-02 / F-03)
    if request.expected_output_schema:
        schema_json = json.dumps(request.expected_output_schema, indent=2, sort_keys=True)
        parts.append(
            f"=== EXPECTED OUTPUT CONTRACT ===\n"
            f"You MUST return your final response/verdict as valid JSON matching this schema:\n"
            f"{schema_json}"
        )

    return "\n\n".join(parts)


@dataclass
class Availability:
    available: bool
    reason: str = ""
    executable: Optional[Executable] = None


class AgentAdapter(ABC):
    """Common NAO-facing interface implemented by every agent adapter."""

    name: str = ""

    def __init__(self, config: AgentConfig, runner: ProcessRunner) -> None:
        self.config = config
        self.runner = runner
        # Concrete adapters declare their name; generic fallbacks adopt the
        # configured agent name so results stay correctly attributed.
        if not self.name:
            self.name = config.name
        self._executable: Optional[Executable] = None
        self._resolved = False

    # -- capability discovery ---------------------------------------------
    @property
    def capabilities(self) -> tuple[Capability, ...]:
        return self.config.capabilities

    def spec(self) -> AgentSpec:
        return self.config.spec()

    @property
    def interfaces(self) -> tuple[str, ...]:
        return self.config.interfaces

    # -- availability ------------------------------------------------------
    def executable(self) -> Optional[Executable]:
        if not self._resolved:
            self._executable = resolve_executable(self.config)
            self._resolved = True
        return self._executable

    def availability(self) -> Availability:
        if not self.config.enabled:
            return Availability(False, "disabled in configuration")
        exe = self.executable()
        if exe is None:
            return Availability(False, "executable not found on PATH or config")
        return Availability(True, "ok", exe)

    def is_available(self) -> bool:
        return self.availability().available

    # -- permission enforcement and validation ----------------------------
    def enforceable_permissions(self) -> frozenset[Permission]:
        """Return the set of NAO permissions this adapter can enforce natively."""
        return frozenset({Permission.READ, Permission.SEARCH, Permission.GIT_READ, Permission.WRITE, Permission.SHELL})

    def command_contract(self) -> NativeArgumentContract:
        """The declarative description of this adapter's native arguments.

        Built from the adapter's explicit configuration. Every native argument
        the adapter may emit must be classified here (harmless, permission-
        bearing or data); anything else fails closed during analysis.
        """
        return contract_from_agent_config(self.name, self.config)

    def validate_permissions(
        self, permissions: "tuple[Permission, ...] | list[Permission]"
    ) -> tuple[bool, str]:
        """Validate that requested permissions can be safely enforced.

        Fails closed: if any permission is unsupported, unmapped, dangerous,
        or would lead to escalation/over-permissioning, returns (False, reason).
        """
        requested = set(permissions)
        if not requested:
            return True, ""

        enforceable = self.enforceable_permissions()
        unsupported = [p.value for p in requested if p not in enforceable]
        if unsupported:
            return (
                False,
                f"UNSUPPORTED_PERMISSION: agent '{self.name}' cannot safely "
                f"enforce permissions: {', '.join(sorted(unsupported))}",
            )

        # Dangerous permissions that cannot be exercised by native agents
        dangerous = requested & {
            Permission.DELETE,
            Permission.SECRET_ACCESS,
            Permission.GIT_PUSH,
            Permission.DEPLOYMENT,
        }
        if dangerous:
            return (
                False,
                f"UNSUPPORTED_PERMISSION: agent '{self.name}' cannot safely enforce "
                f"dangerous permissions: {', '.join(sorted(p.value for p in dangerous))}",
            )

        # Single authoritative native-argument analysis. Every static argument
        # the adapter could emit is classified against the declared contract,
        # *including base_args*: Aider's `--yes` and legacy blanket switches
        # grant capability even though they are not in permission_mapping. Any
        # undeclared flag-like argument fails closed.
        violation = analyze_native_arguments(
            self._static_native_arguments(requested),
            requested,
            self.command_contract(),
        )
        if violation:
            return False, violation

        return True, ""

    def _static_native_arguments(self, requested: set[Permission]) -> list[str]:
        """Every native flag this adapter could emit for ``requested``.

        Mirrors ``permission_args`` but is derived from configuration only, so
        it can be evaluated without building (or running) a command.
        """
        args: list[str] = []
        args.extend(self.config.raw.get("base_args", []))
        args.extend(self.config.extra_args)
        mapping = self.config.permission_mapping
        if not (requested & _MUTATING_PERMISSIONS):
            args.extend(self.config.raw.get("read_only_args", []))
        else:
            for key, spec in mapping.items():
                try:
                    permission = Permission(key)
                except ValueError:
                    continue
                if permission in requested:
                    args.extend(spec.get("allow", []))
        return [str(arg) for arg in args]

    # -- command construction ---------------------------------------------
    def permission_args(self, permissions: "tuple[Permission, ...]") -> list[str]:
        """Translate NAO permissions into native CLI flags.

        Read-only jobs get the conservative flag set; mutating jobs get one
        allow-flag per granted permission. Deny flags are emitted for mapped
        permissions that were *not* granted.
        """
        granted = set(permissions)
        mapping = self.config.permission_mapping
        args: list[str] = []

        read_only_args = self.config.raw.get("read_only_args", [])
        if not (granted & _MUTATING_PERMISSIONS):
            args.extend(read_only_args)
        else:
            for key, spec in mapping.items():
                try:
                    permission = Permission(key)
                except ValueError:
                    continue
                if permission in granted:
                    args.extend(spec.get("allow", []))

        for key, spec in mapping.items():
            try:
                permission = Permission(key)
            except ValueError:
                continue
            if permission not in granted and spec.get("deny"):
                args.extend(spec["deny"])

        return args

    def environment(self, request: AgentRequest) -> dict[str, str]:
        """Return the requested environment, unsanitized.

        The environment policy is applied once, at the final subprocess
        boundary (:meth:`nao.process.ProcessRunner.run`), so an adapter can
        never hand a caller-supplied environment straight to the child.
        """
        return dict(request.environment)

    def session_args(self, request: AgentRequest) -> list[str]:
        if not request.session_id:
            return []
        if not self.spec().supports_feature("session_continuation"):
            return []
        flag = self.config.raw.get("session_flag")
        if flag:
            return [flag, request.session_id]
        if self.name in ("antigravity", "cursor"):
            return ["--conversation-id", request.session_id]
        elif self.name in ("cline", "kilo"):
            return ["--session-id", request.session_id]
        elif self.name == "goose":
            return ["--session", request.session_id]
        return []

    def build_command(
        self, request: AgentRequest, executable: Executable
    ) -> list[str]:
        valid, reason = self.validate_permissions(request.permissions)
        if not valid:
            raise UnsupportedPermissionError(
                self.name, str([p.value for p in request.permissions]), reason
            )

        argv = list(executable.argv)
        base_args = list(self.config.raw.get("base_args", []))
        if request.session_id and self.spec().supports_feature("session_continuation") and self.name == "goose":
            base_args = [arg for arg in base_args if arg != "--no-session"]

        # Assemble the native flags *without* the prompt, analyze the complete
        # set (including base_args), and only then append the prompt. The prompt
        # is free text and must never be parsed as flags.
        native_args: list[str] = []
        native_args.extend(base_args)
        native_args.extend(self.session_args(request))
        native_args.extend(self.permission_args(request.permissions))
        native_args.extend(self.config.extra_args)
        assert_native_arguments_authorized(
            native_args,
            request.permissions,
            self.name,
            self.command_contract(),
        )

        argv.extend(native_args)
        argv.extend(self._prompt_args(request))
        return argv

    def _prompt_args(self, request: AgentRequest) -> list[str]:
        prompt_flag = self.config.raw.get("prompt_flag")
        formatted = format_agent_prompt(request)
        if prompt_flag:
            return [prompt_flag, formatted]
        return [formatted]

    # -- execution ---------------------------------------------------------
    def execute(self, request: AgentRequest) -> AgentResponse:
        valid, reason = self.validate_permissions(request.permissions)
        if not valid:
            return AgentResponse(
                job_id=request.job_id,
                agent=self.name,
                status=JobStatus.FAILED.value,
                errors=[
                    {
                        "failure_class": FailureClass.UNSUPPORTED_PERMISSION.value,
                        "message": reason,
                        "retryable": False,
                    }
                ],
                native_metadata={"unsupported_permission": reason},
            )

        availability = self.availability()
        if not availability.available:
            return AgentResponse(
                job_id=request.job_id,
                agent=self.name,
                status=JobStatus.UNAVAILABLE.value,
                errors=[
                    {
                        "failure_class": FailureClass.UNAVAILABLE.value,
                        "message": availability.reason,
                        "retryable": False,
                    }
                ],
                native_metadata={"availability": availability.reason},
            )

        assert availability.executable is not None
        try:
            argv = self.build_command(request, availability.executable)
        except UnsupportedPermissionError as exc:
            return AgentResponse(
                job_id=request.job_id,
                agent=self.name,
                status=JobStatus.FAILED.value,
                errors=[
                    {
                        "failure_class": FailureClass.UNSUPPORTED_PERMISSION.value,
                        "message": str(exc),
                        "retryable": False,
                    }
                ],
                native_metadata={"unsupported_permission": str(exc)},
            )
        cwd = request.workspace or self.config.raw.get("default_cwd") or os.getcwd()

        # F-01: the adapter forwards the *policy-issued* grant it was handed in
        # the request. It cannot mint one, widen its permissions, or swap its
        # contract - the boundary re-validates the final argv against the grant.
        result = self.runner.run(
            argv=argv,
            cwd=cwd,
            timeout=request.timeout_seconds,
            env=self.environment(request),
            job_id=request.job_id,
            task_id=request.task_id,
            authorization=request.launch_authorization,
            agent=self.name,
        )

        response = self.parse(request, result)
        response.command = argv
        response.exit_code = result.exit_code
        response.duration_seconds = result.duration_seconds
        response.stdout = result.stdout
        response.stderr = result.stderr
        return self._finalize(request, response, result)

    def _finalize(
        self,
        request: AgentRequest,
        response: AgentResponse,
        result: ProcessResult,
    ) -> AgentResponse:
        """Classify the outcome and fill status/errors consistently."""
        if result.cancelled:
            response.status = JobStatus.CANCELLED.value
            return response

        if result.timed_out:
            response.status = JobStatus.TIMEOUT.value
            response.errors.append(
                {
                    "failure_class": FailureClass.TIMEOUT.value,
                    "message": f"timed out after {request.timeout_seconds}s",
                    "retryable": True,
                }
            )
            return response

        if result.error:
            failure = classify_text(result.error)
            if failure is FailureClass.NONE:
                failure = FailureClass.PROCESS_FAILURE
            response.status = JobStatus.FAILED.value
            response.errors.append(
                {
                    "failure_class": failure.value,
                    "message": result.error,
                    "retryable": failure in _RETRYABLE,
                }
            )
            return response

        # Adapter-specific parse may already have decided the status.
        if response.status not in ("succeeded",):
            return response

        if result.exit_code != 0:
            failure = classify_text(result.stderr or result.stdout)
            if failure is FailureClass.NONE:
                failure = FailureClass.PROCESS_FAILURE
            response.status = JobStatus.FAILED.value
            response.errors.append(
                {
                    "failure_class": failure.value,
                    "message": f"agent exited with code {result.exit_code}",
                    "retryable": failure in _RETRYABLE,
                }
            )
        return response

    # -- parsing -----------------------------------------------------------
    @abstractmethod
    def parse(
        self, request: AgentRequest, result: ProcessResult
    ) -> AgentResponse:
        """Normalize native output into an AgentResponse."""

    # -- cancellation ------------------------------------------------------
    def cancel(self, job_id: str) -> bool:
        return self.runner.cancel(job_id)

    def supports_cancellation(self) -> bool:
        return True


class BaseCliAdapter(AgentAdapter):
    """Generic CLI adapter with NDJSON/plain-text parsing.

    Agent-specific adapters subclass this to override parsing or command
    shaping while inheriting availability, permissions, execution, timeouts
    and error classification.
    """

    #: Event types that terminate a run and carry the final answer.
    result_event_types: tuple[str, ...] = ("run_result", "result", "final")

    def parse(
        self, request: AgentRequest, result: ProcessResult
    ) -> AgentResponse:
        response = AgentResponse(
            job_id=request.job_id,
            agent=self.name,
            status=JobStatus.SUCCEEDED.value,
        )
        events = _parse_ndjson(result.stdout)
        terminal = _find_terminal_event(events, self.result_event_types)

        if terminal is not None:
            response.response = _extract_text(terminal)
            response.usage = _extract_usage(terminal)
            response.structured_output = _extract_structured(terminal)
            finish = terminal.get("finishReason") or terminal.get("finish_reason")
            if finish and finish not in ("completed", "success", "stop"):
                response.status = JobStatus.FAILED.value
                response.errors.append(
                    {
                        "failure_class": FailureClass.IMPLEMENTATION_FAILURE.value,
                        "message": f"agent finished with reason '{finish}'",
                        "retryable": True,
                    }
                )
            response.session_id = _extract_session(terminal)
            response.native_metadata["terminal_event"] = terminal
        else:
            # No structured event: fall back to the raw text so nothing is lost.
            text = result.stdout.strip()
            response.response = text or None
            if result.exit_code == 0 and not text:
                response.status = JobStatus.FAILED.value
                response.errors.append(
                    {
                        "failure_class": FailureClass.MALFORMED_OUTPUT.value,
                        "message": "agent produced no parseable output",
                        "retryable": True,
                    }
                )

        response.files_changed = _collect_files_changed(events)
        response.commands_executed = _collect_commands(events)
        response.native_metadata["event_count"] = len(events)
        if events:
            response.native_metadata["events"] = events[-50:]
        return response


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_RETRYABLE = {FailureClass.TOOL_FAILURE, FailureClass.PROCESS_FAILURE,
              FailureClass.MALFORMED_OUTPUT, FailureClass.IMPLEMENTATION_FAILURE,
              FailureClass.RATE_LIMIT}


def _parse_ndjson(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _find_terminal_event(
    events: list[dict[str, Any]], types: tuple[str, ...]
) -> Optional[dict[str, Any]]:
    for event in events:
        if event.get("type") in types:
            return event
    # Some agents emit a single bare JSON object rather than typed events.
    if len(events) == 1 and "type" not in events[0]:
        return events[0]
    return None


def _extract_text(event: dict[str, Any]) -> Optional[str]:
    for key in ("text", "response", "message", "result", "output", "content"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _extract_usage(event: dict[str, Any]) -> Optional[dict[str, Any]]:
    for key in ("usage", "token_usage", "tokens"):
        value = event.get(key)
        if isinstance(value, dict):
            return value
    return None


def _extract_structured(event: dict[str, Any]) -> Optional[dict[str, Any]]:
    for key in ("structured_output", "structuredOutput", "data", "json"):
        value = event.get(key)
        if isinstance(value, dict):
            return value
    return None


def _extract_session(event: dict[str, Any]) -> Optional[str]:
    for key in ("sessionId", "session_id", "conversationId", "conversation_id"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _collect_files_changed(events: list[dict[str, Any]]) -> list[str]:
    changed: list[str] = []
    for event in events:
        for key in ("files_changed", "filesChanged", "files"):
            value = event.get(key)
            if isinstance(value, list):
                changed.extend(str(v) for v in value)
        tool = event.get("tool") or event.get("tool_name")
        if tool in ("write_file", "edit_file", "apply_diff") and isinstance(
            event.get("path"), str
        ):
            changed.append(event["path"])
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for path in changed:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _collect_commands(events: list[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    for event in events:
        command = event.get("command")
        if isinstance(command, str):
            commands.append(command)
        elif isinstance(command, list) and all(
            isinstance(part, str) for part in command
        ):
            commands.append(" ".join(command))
    return commands
