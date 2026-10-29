# NAO — Nexora Agent Orchestrator

NAO is a controlled multi-agent software-engineering orchestration platform. It
receives a task, plans the work, selects appropriate agents, gives each agent
isolated context and permissions, coordinates implementation and verification,
manages Git safely, and returns an auditable final outcome.

Agents are **workers**. NAO owns the workflow. Git is controlled by NAO. Tests
are evidence. Reviews are independent. Permissions are explicit. High-impact
actions require a recorded human approval.

```
Human / Chief Architect
        |
       NAO
        |
Task -> Workflow -> Policy -> Context -> Agent
        |
Execution -> Git -> Tests -> Review
        |
Fix -> Re-test -> Re-review -> Approval -> Merge -> Audit
```

## Architecture

```
nao/
├── cli.py            CLI entry point (python -m nao)
├── services.py       dependency-injection container + bootstrap
├── task_manager.py   task creation, persistence, lifecycle
├── workflow/         state machine + recipes (engine, recipes)
├── router.py         capability- and policy-based agent routing
├── registry.py       agent registry (capabilities, availability)
├── adapters/         unified agent contract + 7 adapters
├── context.py        intentional Context Pack construction
├── policy.py         permission / approval-level engine
├── approval.py       human approval as persisted state
├── execution.py      one-job execution coordinator
├── resource.py       concurrency (max 1 agent) + memory gate
├── git.py            baseline, worktree isolation, diff, commit, merge
├── test_manager.py   real test execution -> TestEvidence
├── review_manager.py independent review findings
├── audit.py          append-only structured event log
├── artifacts.py      structured run persistence
├── sessions.py       agent session continuity
├── safety.py         workspace path validation
├── discovery.py      executable discovery (Windows-safe)
├── process.py        cancellable subprocess runner (shell=False)
└── models/           task, job, agent contract, evidence, findings, events
```

The original Cline prototype (`orchestrator/` and the top-level `adapters/`
package) is preserved and reused: the tested `run_result` parser is imported by
the new Cline adapter, and the legacy adapter now discovers its executable
instead of hard-coding a path.

## Installation

Requires Python 3.10+. Install dependencies:

```bash
pip install -r requirements.txt
```

Verify the environment:

```bash
python -m nao inspect
```

## Web Dashboard

NAO includes a clean, structured, and modern web dashboard providing a visual state machine inspector, agent fleet status, real-time audit logs, diffs, and a human approval gatekeeper:

```bash
python run_web.py
# or
python -m nao web --port 8000
```

Open `http://127.0.0.1:8000` in your browser. Interactive OpenAPI documentation is also available at `http://127.0.0.1:8000/docs`.

## Supported agents

| Agent | Interfaces | Status |
|-------|-----------|--------|
| Cline | CLI / ACP | CLI adapter implemented; live execution requires Cline auth |
| Kilo Code | CLI / ACP | CLI adapter implemented; live execution requires auth |
| Goose | ACP / CLI | CLI adapter implemented; live execution requires auth |
| Aider | CLI scripting | CLI adapter implemented; live execution requires auth |
| Cursor | CLI / ACP | CLI adapter implemented; live execution requires auth |
| GitHub Copilot | CLI programmatic | CLI adapter implemented; live execution requires auth |
| Antigravity | Headless CLI / streaming JSON | CLI adapter implemented; live execution requires auth |

NAO uses each agent's strongest native interface. ACP is treated as an
integration mechanism, not the architecture; adapters isolate all
agent-specific protocol details.

Availability is discovered, not assumed:

```bash
python -m nao agent doctor        # discovery + capability report
python -m nao agent list          # capabilities and interfaces
```

Discovery order: `NAO_<AGENT>_EXECUTABLE` env override → configured
`discovery.paths` → `PATH` → Node-entry fallback (Windows `.cmd` shims are
resolved to the underlying Node script, since batch shims cannot be launched
with `shell=False`).

### Agent authentication

NAO never stores secrets. Configure credentials in each agent's own runtime
(e.g. its own login/config) or through environment variables referenced with
`${ENV:VAR}` in configuration. If an agent is unavailable or unauthenticated,
NAO reports `unavailable` — it never fabricates a successful run.

## Configuration

- `config/agents.json` — per-agent enablement, capabilities, interfaces,
  supported features, timeouts, discovery, permission mapping.
- `config/nao.json` — resource policy, routing rules, approval levels.

Capabilities are declared explicitly (planning, implementation, code review,
testing, debugging, shell, file edit, read-only analysis, structured output,
streaming, session continuation, ACP, CLI, Git/worktree, security review,
documentation). Routing uses the configured per-role preference order plus
capability, availability and permission-compatibility filters — never a
hard-coded "best agent".

## Workflow

Recipes are data. Each recipe is an ordered list of steps executed by the
Workflow Engine, which owns an explicit state machine
(`RECEIVED → ANALYZING → PLANNED → WAITING_FOR_APPROVAL → READY →
IMPLEMENTING → IMPLEMENTED → TESTING → REVIEWING → FIXING → REVERIFYING →
VALIDATED → READY_FOR_MERGE → MERGED → COMPLETED`, with `FAILED`, `BLOCKED`,
`NEEDS_HUMAN`, `CANCELLED` branches). Invalid transitions are rejected and every
transition is audited.

Built-in recipes: `feature_implementation`, `bug_fix`, `refactoring`,
`code_review`, `security_audit`, `testing`, `documentation`.

```bash
python -m nao workflow list
```

Verification is a loop, not a handwave:

```
IMPLEMENT -> TEST -> INDEPENDENT REVIEW -> FINDINGS -> FIX -> TEST AGAIN
-> REVIEW AGAIN -> VALIDATE
```

Fix attempts are bounded by `max_fix_attempts`; exhausting the limit escalates
to `NEEDS_HUMAN` instead of looping forever.

Test steps are real gates (F-04). A failing, missing, errored or timed-out test
never lets the workflow advance toward merge, and review cannot convert a failed
test into a passing one. A zero exit code is **necessary but not sufficient**:
when the runner reports that no tests executed (`Ran 0 tests`) or when no test
count can be established from the output, the gate fails closed (`no_tests` /
`unknown`) instead of passing. `bug_fix` requires a passing regression test
after any fix.

Final verification is **fail-closed** (F-03). The *complete* declared verifier
schema is enforced, not just the `passed` boolean: a missing field, wrong type,
unexpected shape, malformed JSON or ambiguous verdict is a failure
(`NEEDS_HUMAN`), never a default `passed=True`. Reviewer findings are validated
the same way: missing, malformed or wrong-typed reviewer output is a review
**failure**, never treated as "no findings".

The reviewer is a *different* agent than the implementer. If no independent
reviewer is available, the task escalates to `NEEDS_HUMAN` instead of reusing
the implementer.

Sessions are scoped by `(agent, task)`. A task never inherits another task's
session, and a missing task-specific session never falls back to an agent-wide
one; agents without native continuation start fresh.

Cancellation is task-scoped: cancelling task A terminates only A's jobs. On
Windows, child processes are terminated as a tree via `taskkill /PID <pid> /T /F`;
where a platform cannot guarantee process-tree control, NAO still cancels the
launched process and documents the limitation.

## Permissions and approval

NAO has its own permission vocabulary independent of any agent:

| Permission | Level |
|-----------|-------|
| read, search, git_read | 0 automatic |
| write, shell, package_install, migration | 1 controlled (task grant) |
| delete, git_commit, git_merge, network, secret_access | 2 human approval |
| git_push, deployment | 3 always human |

The Policy Engine decides, records the decision, and adapters translate it into
native controls (e.g. `--plan` for read-only Cline runs, `--allow-tool` /
`--deny-tool` for Copilot, `--permission-mode` for Antigravity). NAO never
grants blanket auto-approval just because an agent supports it, and it never
uses `--dangerously-skip-permissions`.

The flow is strictly NAO-authoritative:

```
NAO permission -> Policy Engine -> adapter translation -> native flag -> subprocess
```

Adapter translation **fails closed**. Each adapter declares the permissions it
can safely enforce natively. If a requested permission cannot be represented
safely, the job is refused with `UNSUPPORTED_PERMISSION` before any process is
launched — NAO never silently approximates a dangerous permission.

- `delete`, `secret_access`, `git_push`, `deployment` and other permissions an
  agent cannot natively constrain are refused for every adapter.
- An adapter whose *write* mapping is a blanket switch that also auto-approves
  shell (e.g. Cline/Kilo `--auto-approve=true`, Cursor `--force`) cannot be
  granted `write` without an explicit `shell` grant; otherwise write would
  silently grant shell.
- Only adapters with genuinely granular native controls (e.g. Copilot
  `--allow-tool`/`--deny-tool`) can exercise `write` without `shell`.

Enforcement is centralized in one analyzer (`nao.permissions`) that every entry
point shares:

- **`base_args` are analyzed.** A global flag such as Aider's `--yes` grants
  capability even though it is not in `permission_mapping`; it is therefore only
  accepted when the policy authorizes exactly the capability it grants. Blanket
  switches (`--auto-approve=true`, `--force`, `--yes`,
  `--dangerously-skip-permissions`) count as granting write+shell.
- **Any flag that grants more than the authorized permissions is rejected** — a
  write-only grant cannot imply shell, a shell-only grant cannot imply write,
  and a granular flag (`--allow-tool=shell`) present without that grant is
  rejected.
- **The legacy prototype path uses the same boundary.** `orchestrator/` and the
top-level `adapters/cline.py` call the shared analyzer before launching and no
longer inject `--auto-approve=true`; the read-only demo the prototype issues is
validated as read-only.
- Every agent, Git and test subprocess runs with `shell=False`.

This means some agents are refused for mutating roles under a strict policy.
That is intended: NAO would rather refuse than over-grant. Permission decisions
and refusals are audited (`permission_granted` / `permission_denied` /
`permission_requested`).

### Isolation model

NAO separates five kinds of isolation and never conflates them:

| Kind | What NAO provides |
|------|-------------------|
| Git workspace | Implementation runs in an isolated worktree (`.nao-worktrees/`); the target tree is untouched until an approved merge. |
| Process | Agents are launched with `subprocess` `shell=False`, tracked by job id and cancellable per task. |
| Filesystem containment (cwd) | `WorkspaceGuard` requires the working directory to lie inside the authorized repository or worktree root, and rejects path traversal. This is containment, not a kernel sandbox. | 
| Environment | Every NAO-controlled subprocess — **agent, Git and test** — runs with a **sanitized environment built from an explicit allowlist**; host secret-bearing variables (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*AUTH*`, `*CREDENTIAL*`) are stripped unless explicitly passed. This is applied by `ProcessRunner` by default and by the legacy runner. |
| Network | Modelled as an explicit `network` permission (level 2). It is not silently allowed and no adapter claims to enforce it natively; policies that need network must not rely on NAO to sandbox it. |
| OS sandbox | **Not provided.** There is no kernel-level filesystem or network isolation. |

**Explicit limitation:** a Git worktree is not an OS security boundary and
`shell=False` is not one either. NAO does **not** provide kernel-level
filesystem or network isolation on Windows. Native agent processes run with the
host user's privileges and could, if instructed, read unrelated files or reach
the network. Repository/worktree isolation, cwd containment, environment
sanitization and process isolation reduce accidental exposure; they are not a
security boundary against a malicious agent. True OS-level sandboxing
(containers, VMs, WSL2 jails) is deliberately out of scope for the current 8 GB
Windows target. NAO documents this rather than pretending Python sandboxes
anything.

Approvals are persisted state, decided by an explicit human action:

```bash
python -m nao approvals <task_id>
python -m nao approve <task_id> --subject approval:plan --by you
python -m nao reject  <task_id> --id <approval_id> --by you --note "not yet"
```

## Git safety

NAO owns Git and never lets an agent decide when to commit or merge.

1. inspect state
2. record a baseline (including whether the tree is dirty)
3. create an isolated worktree for implementation work
4. execute the agent inside that workspace
5. collect the diff and changed files
6. validate
7. only then consider commit/merge (behind approval)

A dirty working tree is never overwritten: implementation happens in
`.nao-worktrees/` beside the repository, and merging is refused (escalating to
`NEEDS_HUMAN`) while the target tree has uncommitted changes. `reset --hard` and
`clean -fd` are refused unless the task policy explicitly authorizes them.

Commit and merge are **distinct permissions with distinct approvals**:

```
permission:git_commit -> approval -> commit
permission:git_merge  -> approval -> merge
```

Merge approval is never treated as commit approval. If a real merge conflicts,
the conflict is recorded as evidence, the merge is aborted to restore the known
pre-merge state, and the task escalates to `NEEDS_HUMAN`. NAO never recovers
from a failed merge with destructive `reset`/`clean`.

## Running tests

```bash
python -m unittest discover -s tests -v
```

The suite is offline and deterministic: external agents are replaced with
scripted adapters that implement the same contract. Regression coverage maps to
the audited guarantees, including: fail-closed permission translation (F-01),
real context delivery through the adapter payload (F-02), fail-closed
verification (F-03), test gates (F-04), artifact redaction (F-05),
environment and workspace isolation (F-06), safe merge-conflict recovery (F-07),
explicit commit/merge approval (F-08), target-repository context and traversal
prevention (F-09), session isolation (F-11), independent reviewer routing
(F-12), task-scoped cancellation and Windows process-tree cleanup (F-13),
structured test commands (F-14), audit degradation (F-15), recipe role routing
(F-16), and resource limits (F-17).

Live agent tests are opt-in and are not part of the default suite. NAO was
verified at the **discovery** level for the agents installed on this machine
(Cline, Kilo, Goose, Aider). Live **execution** was not run because it requires
per-agent authentication and may incur cost; each adapter's command shaping and
parsing are covered by unit tests and must be validated live before relying on
them.

## CLI

```bash
python -m nao inspect
python -m nao agent doctor
python -m nao workflow list

python -m nao task create --recipe feature_implementation \
    --description "Add chess move validation" \
    --criteria "Rejects illegal moves" \
    --repo /path/to/repo
python -m nao task run <task_id>
python -m nao task status <task_id>
python -m nao task cancel <task_id>

python -m nao run status <task_id>
python -m nao run jobs <task_id>
python -m nao run logs <task_id>     # audit event stream
python -m nao review <task_id>
python -m nao audit <task_id>

python -m nao git status
python -m nao test --repo /path/to/repo
```

Add `--json` before the subcommand for machine-readable output.

## Run artifacts

```
runs/<task_id>/task.json            task + workflow progress
runs/<task_id>/jobs/<job_id>.json   job + normalized AgentResponse
runs/<task_id>/diffs/<job_id>.diff  collected diff
runs/<task_id>/tests/*.json         TestEvidence
runs/<task_id>/findings.json        review findings
runs/<task_id>/approvals.json       approval state
runs/<task_id>/contexts/*.txt       the exact Context Pack sent
audit/<task_id>/events.ndjson       append-only structured events
```

**Redaction is a single boundary (F-05).** Every persisted artifact — task and
job records, agent responses, prompts, stdout, stderr, diffs, test evidence,
task metadata, review findings, approval state, session state, the legacy
result file and audit events — passes through one recursive redaction function
(`redact_data`/`redact_text`) before it is written. Secret-named fields are
recognised by *component*, not exact match, so `SECRET`, `TOKEN`, `API_KEY`,
`APIKEY`, `PASSWORD`, `PASSWD`, `CLIENT_SECRET`, `PRIVATE_KEY`, `AUTH_TOKEN`,
`ACCESS_TOKEN`, `GITHUB_TOKEN` and casing/prefix variants such as
`AWS_SECRET_ACCESS_KEY` are all redacted; env references, PEM private keys and
known token shapes (`sk-*`, GitHub, Slack, `Bearer`) are masked in free text.

Redaction is **pattern-based** and therefore **not an absolute security
boundary**: a secret that matches no known key name or token shape can still be
persisted. It preserves structured JSON and useful diagnostics rather than
guaranteeing detection of every possible secret.

**Audit durability.** If durable audit persistence fails, NAO does not silently
fall back to memory-only. It marks the audit state *degraded*, keeps the events
in memory, and surfaces `audit_degraded` in the task result, the `--json` output
and the `nao task run` exit path — a run is never reported as a fully-audited
success while its audit storage is degraded.

## Test command format

The test command is an explicit argv array, never a shell string:

```json
"test_command": ["pytest", "-q"]
```

A string form (e.g. `"pytest -q"`) is accepted for convenience and split into
an argv array with `shlex` (`posix=False`, so Windows paths survive). NAO never
uses `shell=True`. Use `test_cwd` to run from a specific directory.

A configured gate must show that tests actually ran: an exit code of `0` with
`Ran 0 tests`, or with no recognizable pass/fail/error summary, is recorded as
`no_tests` / `unknown` and fails the gate. Set `require_tests: false` on a plan
only for commands that are legitimately not test suites.

## Resource management

The Resource Manager tracks active jobs, RAM, CPU, disk and the configured
concurrency limit. Before launch it gates on a semaphore and on available RAM;
when it cannot safely launch it *waits* (auditing `resource_wait`) rather than
crashing the machine. Default `max_concurrent_agents` is **1** for the 8 GB
target; parallel agent execution is intentionally not enabled.

## Limitations

- **No OS-level sandbox.** On Windows, NAO provides workspace, environment and
  process isolation (above), not kernel filesystem/network isolation. A hostile
  or misbehaving agent runs with host user privileges. Do not treat NAO as a
  security boundary against a malicious agent.
- ACP is **not implemented**. ACP is declared in configuration/interfaces and
  reserved for a later task; only CLI interfaces are wired. There is no fake ACP
  path.
- Live execution of the seven agents was not verified in this environment
  (authentication and cost). Command shapes are configuration-driven and
  parsing is unit-tested; validate live before production use.
- Windows process-tree termination relies on `taskkill /T`; where that is
  unavailable NAO still terminates the launched process but cannot guarantee
  grandchild cleanup.
- Concurrency is 1 by design (8 GB RAM machine).
- Context file selection is explicit (`relevant_files` / `relevant_globs`);
  automatic relevance ranking is not implemented, and NAO architecture docs are
  only included when the task asks for them.
- The merge step pauses on a dirty target tree by design rather than attempting
  an automatic resolution, and aborts the merge (rather than force-resolving)
  when a real conflict occurs.
