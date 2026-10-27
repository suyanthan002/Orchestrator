# NAO — Final Orchestration Architecture

The goal should **not** be:

> "Make one Python program capable of launching seven CLIs."

That's just a launcher.

The goal is:

> **NAO is a controlled multi-agent software-engineering orchestration platform that receives a task, plans the work, selects appropriate agents, gives each agent isolated context and permissions, coordinates implementation and verification, evaluates results, manages Git safely, and returns an auditable final outcome to the human/Chief Architect.**

---

# 1. The fundamental architecture

I recommend this:

```text
                         HUMAN
                           │
                           │ task / approval
                           ▼
                  ┌──────────────────┐
                  │  CHIEF ARCHITECT │
                  │    ChatGPT       │
                  └────────┬─────────┘
                           │
                    Architecture /
                    Task Specification
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                         NAO CORE                             │
│                                                             │
│  ┌──────────────┐       ┌────────────────┐                 │
│  │ Task Manager │──────▶│ Workflow Engine│                 │
│  └──────────────┘       └───────┬────────┘                 │
│                                  │                          │
│        ┌─────────────────────────┼──────────────────┐       │
│        ▼                         ▼                  ▼       │
│  Agent Router             Context Manager     Policy Engine │
│        │                         │                  │       │
│        ▼                         ▼                  ▼       │
│  Agent Registry             Context Pack       Permissions │
│        │                                                │  │
│        └──────────────────┬─────────────────────────────┘  │
│                           ▼                                │
│                    Execution Manager                       │
│                           │                                │
│              ┌────────────┼─────────────┐                  │
│              ▼            ▼             ▼                  │
│          Git Manager  Test Manager  Artifact Manager        │
│              │            │             │                  │
│              └────────────┼─────────────┘                  │
│                           ▼                                │
│                     Audit / Event Log                       │
└───────────────────────────┬─────────────────────────────────┘
                            │
             controlled agent execution
                            │
       ┌────────┬────────┬─┴───────┬────────┬────────┐
       ▼        ▼        ▼         ▼        ▼        ▼
     Cline    Kilo     Goose     Aider   Copilot  Cursor
                                                    │
                                               Antigravity
```

The **important idea** is that agents never become the architecture of NAO.

NAO owns the workflow.

---

# 2. Seven agents should NOT be treated equally

This is important.

NAO should have an **Agent Capability Registry**.

Each agent declares capabilities rather than simply:

```json
"cline": true
```

For example:

```json
{
  "name": "cline",
  "capabilities": [
    "implementation",
    "planning",
    "code_review",
    "testing"
  ],
  "interfaces": [
    "cli",
    "acp"
  ],
  "supports": {
    "read_only": true,
    "file_edit": true,
    "shell": true,
    "streaming": true,
    "structured_output": true
  }
}
```

Another agent may have different capabilities.

This allows NAO to ask:

> Which available agent can perform this job under these permissions?

rather than:

> Which agent do I like?

---

# 3. Agent roles

NAO should support **roles** independent of agent names.

For example:

### Architect

Planning, decomposition, architecture reasoning.

### Implementer

Actually changes code.

### Reviewer

Examines implementation without changing it.

### Tester

Runs tests and diagnoses failures.

### Debugger

Investigates a failure and proposes/fixes it.

### Security reviewer

Checks security-specific concerns.

### Documentation agent

Updates required documentation.

### Final verifier

Checks whether the original acceptance criteria are actually satisfied.

Then NAO maps roles to available agents.

For example:

```text
IMPLEMENTER
    ↓
Cursor / Antigravity / Cline / Kilo / Aider

REVIEWER
    ↓
Copilot / Cline / Cursor / Kilo

TESTER
    ↓
Copilot / Cline / Goose / Cursor

SECURITY
    ↓
Copilot / Cursor / Cline
```

This is much more powerful than hard-coding seven agents.

---

# 4. The workflow engine

This should be the **heart of NAO**.

A task should move through explicit states.

```text
RECEIVED
   ↓
ANALYZING
   ↓
PLANNED
   ↓
WAITING_FOR_APPROVAL
   ↓
READY
   ↓
IMPLEMENTING
   ↓
IMPLEMENTED
   ↓
TESTING
   ↓
REVIEWING
   ↓
FIXING
   ↓
REVERIFYING
   ↓
VALIDATED
   ↓
READY_FOR_MERGE
   ↓
MERGED
   ↓
COMPLETED
```

Failure branches:

```text
                  ┌── FAILED
                  │
RUNNING ──────────┼── BLOCKED
                  │
                  └── NEEDS_HUMAN
```

This gives us something extremely important:

**NAO always knows where a task is.**

---

# 5. Agent execution should be a job, not a subprocess

The current runner is too primitive for the final system.

Eventually:

```text
AgentJob
```

should contain:

```text
job_id
task_id
agent
role
workspace
prompt
context
permissions
timeout
environment
input artifacts
expected output schema
attempt number
parent job
```

And the result:

```text
AgentJobResult
├── status
├── exit_code
├── response
├── structured_result
├── stdout
├── stderr
├── files_changed
├── git_diff
├── tests_run
├── tests_passed
├── usage
├── duration
├── errors
└── artifacts
```

This is what makes the system auditable.

---

# 6. Context Manager

This is one of the most important components.

We don't want to send an entire project to every agent.

NAO builds a **Context Pack**.

```text
Context Pack
│
├── Task description
├── Acceptance criteria
├── Relevant architecture
├── Relevant files
├── Relevant documentation
├── Previous agent findings
├── Test results
├── Git state
├── Constraints
├── Permission policy
└── Previous failures
```

Example:

```text
Task:
Implement Chess move validation.

Relevant:
backend/apps/chess/models.py
frontend/src/chess/ChessBoard.jsx
docs/MODULES/chess.md

Constraints:
Do not change authentication.
Do not modify inventory.
Use existing ChessGame model.

Acceptance:
...
```

This prevents context explosion.

---

# 7. Shared memory vs task memory

NAO should have two kinds of knowledge.

### Project knowledge

Persistent:

```text
architecture
conventions
technology
constraints
important decisions
```

### Task knowledge

Temporary:

```text
task
agent responses
test failures
diffs
attempts
review findings
```

Never mix these casually.

---

# 8. Permission Engine

This is absolutely essential.

NAO should never simply say:

```text
--auto-approve=true
```

globally.

Instead:

```text
Permission Policy
```

could be:

```text
READ
  allowed

SEARCH
  allowed

WRITE
  requires task permission

SHELL
  restricted

GIT READ
  allowed

GIT COMMIT
  controlled

GIT PUSH
  human approval

DELETE
  restricted

NETWORK
  restricted

PACKAGE INSTALL
  controlled

DATABASE MIGRATION
  controlled

DEPLOYMENT
  human approval
```

This becomes agent-independent.

For example, Antigravity currently has scoped permission rules and headless mode; its documentation explicitly distinguishes default permissions from `--dangerously-skip-permissions`. ([Google Antigravity][2])

Copilot CLI likewise exposes tool allow/deny controls, including restrictions on shell/file/network capabilities. ([GitHub Docs][3])

So NAO should have its own policy and then translate that policy into each agent's native controls.

---

# 9. Git Manager

This should be one of NAO's strongest components.

Never allow seven agents to casually modify:

```text
D:\Nexora
```

at the same time.

Instead:

```text
                    MAIN REPOSITORY
                           │
                           ▼
                    NAO Git Manager
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
          Worktree A   Worktree B   Worktree C
             │             │             │
          Agent 1       Agent 2       Reviewer
```

Cursor itself now supports CLI worktrees, which reinforces this model. ([Cursor][4])

NAO should control:

```text
branch creation
worktree creation
baseline commit
diff collection
commit creation
merge
conflict detection
rollback
cleanup
```

### Critical rule

Agents do **not** decide when to merge.

NAO decides.

And eventually:

```text
NAO
 ↓
Human approval
 ↓
merge
```

for important changes.

---

# 10. Verification pipeline

This is where NAO becomes genuinely useful.

Suppose Cursor implements something.

NAO shouldn't trust:

> "Done."

Instead:

```text
Cursor
 ↓
Implementation
 ↓
NAO collects diff
 ↓
Tests
 ↓
Independent Reviewer
 ↓
Findings?
 ├── NO → validation
 └── YES
       ↓
     Fix agent
       ↓
     Tests
       ↓
     Reviewer
```

So:

```text
IMPLEMENT
   ↓
SELF TEST
   ↓
INDEPENDENT REVIEW
   ↓
FIX
   ↓
RETEST
   ↓
REVIEW AGAIN
```

This directly matches the workflow you've been using for Nexora.

---

# 11. Independent verification must actually be independent

This is subtle.

Don't do:

```text
Cursor implemented
       ↓
Cursor says it's correct
```

That isn't independent verification.

Instead:

```text
Cursor
  │
  │ implementation
  ▼
NAO
  │
  ▼
Copilot / Cline / Kilo
  │
  │ independent review
  ▼
Findings
```

The reviewer gets:

* task requirements
* acceptance criteria
* resulting diff
* relevant files

but **not blindly the implementer's conclusion**.

---

# 12. Agent communication

Agents should **not** communicate directly.

Avoid:

```text
Cursor ↔ Cline
Cline ↔ Goose
Goose ↔ Kilo
...
```

That creates a spaghetti system.

Instead:

```text
             NAO
          /   |   \
         /    |    \
    Cursor  Cline  Goose
         \    |    /
          \   |   /
            NAO
```

Agent A produces:

```text
AgentResult
```

NAO decides what information Agent B receives.

---

# 13. ACP should be a major integration layer

This is where our original architecture can become much cleaner.

Cline exposes ACP. ([GitHub][1])

Kilo exposes ACP. ([Kilo][5])

Goose exposes ACP. ([GitHub][6])

Cursor CLI also supports ACP via `agent acp`. ([Cursor][4])

So NAO should eventually have:

```text
                    NAO Agent Interface
                           │
             ┌─────────────┴──────────────┐
             │                            │
          ACP Adapter                CLI Adapter
             │                            │
       ┌─────┼─────┐             ┌───────┼────────┐
       │     │     │             │       │        │
     Cline Kilo Goose          Aider   Copilot  Antigravity
       │           │                              │
     Cursor        ...                           CLI
```

**But don't force ACP where it isn't the best interface.**

NAO should use the strongest native interface available for each agent.

---

# 14. Current interface strategy

Based on the current documentation:

| Agent       | NAO integration candidate     |
| ----------- | ----------------------------- |
| Cline       | CLI / ACP                     |
| Kilo Code   | CLI / ACP                     |
| Goose       | ACP / CLI                     |
| Aider       | CLI scripting                 |
| Cursor      | CLI / ACP                     |
| Copilot     | CLI programmatic              |
| Antigravity | Headless CLI / streaming JSON |

Cline's CLI already supports JSON output and ACP. ([GitHub][7])

Kilo has ACP and CLI automation capabilities. ([Kilo][5])

Goose supports ACP over stdio and also an HTTP/WebSocket ACP server. ([GitHub][6])

Aider explicitly supports scripted one-shot execution using `--message`, `--message-file`, `--yes`, and dry-run controls. ([Aider][8])

Cursor now has non-interactive CLI automation and ACP. ([Cursor][9])

Copilot CLI supports programmatic prompts and tool permission controls. ([GitHub Docs][10])

Antigravity is particularly interesting: its headless CLI supports JSON/NDJSON output, structured output schemas, streaming sessions, conversation IDs, and programmatic stdin-driven multi-turn sessions. ([Google Antigravity][2])

So **we don't need to pretend all seven agents work the same way.**

NAO should normalize their differences.

---

# 15. Unified Agent Contract

Every adapter should translate its native interface into:

```text
NAO Agent Contract
```

### Input

```text
AgentRequest
├── job_id
├── role
├── task
├── context
├── workspace
├── permissions
├── timeout
├── model configuration
└── expected output schema
```

### Output

```text
AgentResponse
├── status
├── response
├── structured_output
├── files_changed
├── commands_executed
├── tests
├── usage
├── duration
├── errors
└── native_metadata
```

Therefore:

```text
Cline native result ──┐
Kilo native result ───┤
Goose native result ──┤
Aider native result ──┤──▶ NAO AgentResponse
Cursor native result ─┤
Copilot native result ┤
Antigravity result ──┘
```

---

# 16. Workflow recipes

Instead of hard-coding workflows, NAO should have **recipes**.

Example:

```text
feature_implementation
```

```text
1. analyze
2. plan
3. human approval
4. create worktree
5. implement
6. run tests
7. independent review
8. fix findings
9. rerun tests
10. final review
11. human approval
12. merge
```

Another:

```text
bug_fix
```

```text
1. reproduce
2. diagnose
3. propose fix
4. implement
5. test
6. review
7. regression test
8. merge
```

Another:

```text
security_audit
```

```text
1. inspect
2. threat analysis
3. independent review
4. findings
5. optional fixes
6. verification
```

---

# 17. Agent selection should be capability-based

NAO should eventually calculate something like:

```text
Task requires:

implementation
Python
Django
Git
file editing
test execution
moderate complexity
```

Then registry:

```text
Cursor       ✓
Antigravity  ✓
Cline        ✓
Kilo         ✓
Goose        ✓
Aider        ✓
Copilot      ✓
```

Then additional constraints:

```text
Currently authenticated?
Available?
Permission compatible?
Cost policy?
Model available?
Previous failures?
Task complexity?
```

Then choose according to **configured policy**, not an arbitrary hard-coded ranking.

Important: NAO should **not automatically assume "best agent"**. It should use explicit routing rules.

---

# 18. 8 GB RAM constraint

This remains a major design constraint.

We should build:

```text
MAX_CONCURRENT_AGENTS = 1
```

initially.

Even though Antigravity itself supports concurrent subagents, we don't want NAO spawning multiple heavyweight local processes on an 8 GB machine. Antigravity's platform explicitly supports parallel subagents, but that's a capability we can leave disabled in NAO's local resource policy. ([Google Antigravity][11])

Later:

```text
max_concurrency = 1
```

could become:

```text
1 → safe
2 → optional
3+ → disabled by hardware policy
```

NAO should therefore have a **Resource Manager**.

---

# 19. Resource Manager

It tracks:

```text
RAM
CPU
active processes
agent process count
timeouts
disk usage
```

Before launching:

```text
Can I safely launch this agent?
```

If not:

```text
WAIT
```

rather than crash the machine.

---

# 20. Human approval gates

NAO should never become an uncontrolled autonomous coding system.

Define approval levels.

### Level 0 — automatic

```text
read files
inspect Git
run safe tests
analyze
```

### Level 1 — controlled

```text
edit files
install dependencies
run migrations
```

### Level 2 — human approval

```text
commit
merge
delete substantial files
change architecture
modify secrets/configuration
```

### Level 3 — always human

```text
push
deployment
production DB
credentials
destructive infrastructure actions
```

---

# 21. Audit system

Every event should be recorded.

```text
audit/
   task.json
   events.ndjson
   agents/
   diffs/
   tests/
   decisions/
```

Example event:

```json
{
  "timestamp": "...",
  "task_id": "...",
  "event": "agent_completed",
  "agent": "cursor",
  "role": "implementer",
  "status": "success"
}
```

This means months later you can answer:

> Why did NAO change this file?

and trace:

```text
human request
 ↓
task
 ↓
workflow
 ↓
agent
 ↓
prompt
 ↓
tool calls
 ↓
diff
 ↓
test
 ↓
review
 ↓
approval
 ↓
merge
```

That's the level I think you actually want.

---

# 22. Failure handling

Every agent failure should become structured.

```text
Agent failed
     │
     ├── transient?
     │      └── retry
     │
     ├── authentication?
     │      └── human
     │
     ├── rate limit?
     │      └── alternate agent
     │
     ├── tool failure?
     │      └── retry / alternate
     │
     ├── code failure?
     │      └── verifier/debugger
     │
     └── architectural conflict?
            └── Chief Architect
```

This is much better than simply:

```text
subprocess returned 1
```

---

# 23. Session continuity

NAO should preserve:

```text
agent session ID
conversation ID
task ID
job ID
```

Where an agent supports session continuation, NAO can resume it.

Antigravity, for example, exposes conversation IDs and supports continuing previous conversations in headless mode. ([Google Antigravity][2])

This lets NAO say:

```text
Cursor, continue from your previous implementation session.
```

rather than rebuilding the entire context.

---

# 24. The final architecture

So I would freeze NAO around these components:

```text
NAO
│
├── API / CLI
│
├── Task Manager
│
├── Workflow Engine
│
├── Workflow Recipes
│
├── Agent Registry
│
├── Agent Router
│
├── Agent Adapters
│   ├── Cline
│   ├── Kilo
│   ├── Goose
│   ├── Aider
│   ├── Cursor
│   ├── Copilot
│   └── Antigravity
│
├── Context Manager
│
├── Permission / Policy Engine
│
├── Execution Manager
│
├── Resource Manager
│
├── Git Manager
│
├── Test Manager
│
├── Review Manager
│
├── Artifact Manager
│
├── Session Manager
│
├── Audit/Event Logger
│
└── Human Approval Manager
```

---

# 25. And this is how a real Nexora task would flow

Suppose you tell ChatGPT:

> Implement the next Chess backend feature.

ChatGPT produces the architecture/specification.

Then:

```text
ChatGPT
   │
   ▼
NAO
   │
   ├── validates specification
   │
   ├── creates task
   │
   ├── analyzes repository
   │
   ├── builds context pack
   │
   ├── selects implementation agent
   │
   ├── creates isolated Git worktree
   │
   ▼
Cursor
   │
   ├── implements
   └── reports
   │
   ▼
NAO
   │
   ├── collects diff
   ├── runs tests
   │
   ▼
Copilot
   │
   └── independent audit
   │
   ▼
NAO
   │
   ├── findings?
   │
   ├──── yes ──▶ Fix agent
   │
   └──── no
   │
   ▼
Final validation
   │
   ▼
Human approval
   │
   ▼
Git merge
   │
   ▼
Audit record
   │
   ▼
ChatGPT
```

That's the **orchestra**.

---

## One important change to our earlier plan

I **would not** make ACP the entire architecture.

ACP should be an **adapter/integration mechanism**.

The architecture should be:

```text
                 NAO CONTRACT
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
      ACP Adapter           Native CLI Adapter
          │                     │
     Cline/Kilo/Goose       Aider/Copilot/
        /Cursor             Antigravity/etc.
```

That protects us from being locked to any one protocol.

---

# What Cursor should build

Now we can use Cursor **properly**.

Instead of giving Cursor:

> "Build an agent router."

we give it a **complete NAO Architecture Specification** containing:

1. architecture
2. component responsibilities
3. data contracts
4. workflow state machine
5. agent contract
6. adapter contract
7. permission model
8. Git isolation model
9. testing model
10. audit model
11. resource constraints
12. seven-agent integration requirements
13. failure/retry rules
14. human approval rules
15. current prototype that must be preserved
16. acceptance tests

Then Cursor becomes the **implementation agent**, while **I remain the architecture/review layer**.

That fits your original Nexora philosophy much better:

```text
                 YOU
                  │
                  ▼
             ChatGPT
          Chief Architect
                  │
          FINAL SPECIFICATION
                  │
                  ▼
              CURSOR
            Implementation
                  │
                  ▼
                 NAO
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
      Cline     Kilo      Goose ...
                  │
                  ▼
             Verification
                  │
                  ▼
             ChatGPT
          Architecture Audit
```

