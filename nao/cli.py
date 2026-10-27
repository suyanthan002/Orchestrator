"""NAO command-line interface (plan.md section 30).

Human-readable by default, machine-readable with ``--json``. The CLI is a thin
presentation layer over the Task Manager and the services; it contains no
orchestration logic and no agent-specific commands.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from .git import GitManager
from .services import build_services
from .task_manager import TaskManager
from .workflow.recipes import list_recipes


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nao", description="NAO - Nexora Agent Orchestrator"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("inspect", help="show configuration and available agents")

    agent = sub.add_parser("agent", help="agent operations").add_subparsers(
        dest="agent_command", required=True
    )
    agent.add_parser("list", help="list configured agents")
    agent.add_parser("doctor", help="check agent availability")

    workflow = sub.add_parser("workflow", help="workflow operations").add_subparsers(
        dest="workflow_command", required=True
    )
    workflow.add_parser("list", help="list workflow recipes")

    task = sub.add_parser("task", help="task operations").add_subparsers(
        dest="task_command", required=True
    )
    create = task.add_parser("create", help="create a task")
    create.add_argument("--description", required=True)
    create.add_argument("--title", default="")
    create.add_argument("--recipe", default="feature_implementation")
    create.add_argument("--repo", default=None)
    create.add_argument("--criteria", action="append", default=[])
    create.add_argument("--allow", action="append", default=[], help="grant a permission")
    create.add_argument("--max-fix-attempts", type=int, default=2)
    create.add_argument("--max-jobs-per-role", type=int, default=1)
    create.add_argument("--relevant-file", action="append", default=[])
    create.add_argument("--test-command", default=None)

    run = task.add_parser("run", help="run a task")
    run.add_argument("task_id")
    status = task.add_parser("status", help="task status")
    status.add_argument("task_id")
    cancel = task.add_parser("cancel", help="cancel a task")
    cancel.add_argument("task_id")
    task.add_parser("list", help="list tasks")

    runs = sub.add_parser("run", help="run operations").add_subparsers(
        dest="run_command", required=True
    )
    r_status = runs.add_parser("status", help="run status")
    r_status.add_argument("task_id")
    r_logs = runs.add_parser("logs", help="run audit log")
    r_logs.add_argument("task_id")
    r_jobs = runs.add_parser("jobs", help="run jobs")
    r_jobs.add_argument("task_id")

    review = sub.add_parser("review", help="show review findings")
    review.add_argument("task_id")

    approvals = sub.add_parser("approvals", help="list approvals")
    approvals.add_argument("task_id")

    approve = sub.add_parser("approve", help="grant an approval")
    approve.add_argument("task_id")
    approve.add_argument("--id", dest="approval_id", default=None)
    approve.add_argument("--subject", default=None)
    approve.add_argument("--by", default="human")
    approve.add_argument("--note", default=None)

    reject = sub.add_parser("reject", help="deny an approval")
    reject.add_argument("task_id")
    reject.add_argument("--id", dest="approval_id", default=None)
    reject.add_argument("--subject", default=None)
    reject.add_argument("--by", default="human")
    reject.add_argument("--note", default=None)

    git = sub.add_parser("git", help="git operations").add_subparsers(
        dest="git_command", required=True
    )
    g_status = git.add_parser("status", help="repository state")
    g_status.add_argument("--repo", default=None)

    test = sub.add_parser("test", help="run the repository test suite")
    test.add_argument("--repo", default=None)
    test.add_argument("--scope", default=None)
    test.add_argument("--kind", default="project")

    audit = sub.add_parser("audit", help="show the audit log")
    audit.add_argument("task_id")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    services = build_services()
    manager = TaskManager(services)
    as_json = getattr(args, "json", False)

    def emit(data: Any, human: str) -> int:
        if as_json:
            _print_json(data)
        else:
            print(human)
        return 0

    # -- inspect -----------------------------------------------------------
    if args.command == "inspect":
        data = {
            "project_root": str(services.project_root),
            "agents": services.registry.doctor(),
            "resources": vars(services.config.resources),
            "routing": {
                role.value: names
                for role, names in services.config.routing.items()
            },
        }
        if as_json:
            _print_json(data)
        else:
            print("NAO - Nexora Agent Orchestrator")
            print(f"Project root : {data['project_root']}")
            print(f"Agents       : {len(data['agents'])} configured")
            for item in data["agents"]:
                mark = "ok " if item["available"] else "-- "
                print(
                    f"  [{mark}] {item['agent']:<12} {item['reason']}"
                    + (f" ({item['executable']})" if item["executable"] else "")
                )
            print(
                "Max concurrent agents: "
                f"{services.config.resources.max_concurrent_agents}"
            )
        return 0

    # -- agents ------------------------------------------------------------
    if args.command == "agent":
        if args.agent_command == "list":
            specs = [
                services.registry.spec(name).to_dict()
                for name in services.registry.names()
            ]
            return emit(specs, "\n".join(
                f"{s['name']:<12} enabled={s['enabled']} "
                f"caps={len(s['capabilities'])} ifaces={s['interfaces']}"
                for s in specs
            ))
        report = services.registry.doctor()
        return emit(report, "\n".join(
            f"{r['agent']:<12} {'available' if r['available'] else 'unavailable'}"
            f" - {r['reason']}"
            + (f"\n             {r['executable']}" if r["executable"] else "")
            for r in report
        ))

    # -- workflows ---------------------------------------------------------
    if args.command == "workflow":
        recipes = [
            {
                "name": r.name,
                "description": r.description,
                "mutating": r.mutating,
                "steps": [s.kind for s in r.steps],
            }
            for r in list_recipes()
        ]
        return emit(recipes, "\n".join(
            f"{r['name']:<22} {'(mutating)' if r['mutating'] else '(read-only)'} "
            f"{' -> '.join(r['steps'])}" for r in recipes
        ))

    # -- tasks -------------------------------------------------------------
    if args.command == "task":
        if args.task_command == "create":
            metadata: dict[str, Any] = {}
            if args.relevant_file:
                metadata["relevant_files"] = args.relevant_file
            if args.test_command:
                metadata["test_command"] = args.test_command
            task = manager.create_task(
                description=args.description,
                title=args.title,
                recipe=args.recipe,
                acceptance_criteria=args.criteria,
                repo_path=args.repo,
                granted_permissions=args.allow or None,
                max_fix_attempts=args.max_fix_attempts,
                max_jobs_per_role=args.max_jobs_per_role,
                metadata=metadata,
            )
            return emit(
                task.to_dict(),
                f"created {task.task_id} (recipe={task.recipe}, "
                f"state={task.state.value})\n"
                f"permissions: {', '.join(p.value for p in task.granted_permissions) or 'none'}",
            )

        if args.task_command == "list":
            task_ids = manager.list_tasks()
            return emit(task_ids, "\n".join(task_ids) if task_ids else "(no tasks)")

        if args.task_command == "status":
            data = manager.status(args.task_id)
            return emit(data, json.dumps(data, indent=2))

        if args.task_command == "cancel":
            ok = manager.cancel(args.task_id)
            return emit({"cancelled": ok}, "cancelled" if ok else "task not found")

        if args.task_command == "run":
            result = manager.run_task(args.task_id)
            data = result.to_dict()
            if as_json:
                _print_json(data)
            else:
                print(f"Task    : {result.task_id}")
                print(f"State   : {result.state.value}")
                print(f"Success : {result.success}")
                print(f"Message : {result.message}")
                if result.worktree_branch:
                    print(f"Branch  : {result.worktree_branch}")
                print(f"Jobs    : {len(result.jobs)}")
                for job in result.jobs:
                    print(
                        f"  - {job.role.value:<16} {job.agent:<12} "
                        f"{job.status.value:<10} attempt {job.attempt}"
                    )
                if result.evidence:
                    print("Tests   :")
                    for e in result.evidence:
                        print(f"  - {e.kind}: {e.status} (exit {e.exit_code})")
                if result.findings:
                    print("Findings:")
                    for f in result.findings:
                        print(
                            f"  - [{f.severity}/{f.status.value}] {f.title}"
                        )
                pending = manager.pending_approvals(result.task_id)
                if pending:
                    print("Pending approvals:")
                    for p in pending:
                        print(f"  - {p['approval_id']} {p['subject']}: {p['reason']}")
            if result.audit_degraded:
                # F-15: never advertise a fully-audited completion when durable
                # audit persistence failed.
                print(
                    "WARNING: audit persistence is DEGRADED; this run is not "
                    "fully audited and needs human review.",
                    file=sys.stderr,
                )
            return 0 if (result.success and not result.audit_degraded) else 1

    # -- run ---------------------------------------------------------------
    if args.command == "run":
        if args.run_command == "status":
            data = manager.status(args.task_id)
            return emit(data, json.dumps(data, indent=2))
        if args.run_command == "jobs":
            jobs = manager.jobs(args.task_id)
            return emit(jobs, json.dumps(jobs, indent=2))
        if args.run_command == "logs":
            events = manager.audit_events(args.task_id)
            if as_json:
                _print_json(events)
            else:
                for event in events:
                    print(
                        f"{event.get('timestamp','')} "
                        f"{event.get('event'):<22} "
                        f"{event.get('status',''):<12} "
                        f"{event.get('message','')}"
                    )
            return 0

    # -- review ------------------------------------------------------------
    if args.command == "review":
        findings = manager.findings(args.task_id)
        return emit(findings, json.dumps(findings, indent=2) if findings else "(no findings)")

    # -- approvals ---------------------------------------------------------
    if args.command == "approvals":
        pending = manager.pending_approvals(args.task_id)
        all_approvals = [a.to_dict() for a in services.approvals.all(args.task_id)]
        return emit(all_approvals, json.dumps(all_approvals, indent=2))

    if args.command in ("approve", "reject"):
        approval_id = args.approval_id
        if approval_id is None and args.subject:
            for pending in services.approvals.pending(args.task_id):
                if pending.subject == args.subject:
                    approval_id = pending.approval_id
                    break
        if approval_id is None:
            print("error: specify --id or a pending --subject", file=sys.stderr)
            return 2
        try:
            if args.command == "approve":
                record = manager.approve(
                    args.task_id, approval_id, args.by, args.note
                )
            else:
                record = manager.reject(
                    args.task_id, approval_id, args.by, args.note
                )
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return emit(record, f"{record['state']}: {record['subject']}")

    # -- git ---------------------------------------------------------------
    if args.command == "git":
        repo = args.repo or services.config.default_repo_path
        state = GitManager(repo, audit=services.audit, runner=services.runner).state()
        data = state.to_dict()
        if as_json:
            _print_json(data)
        else:
            print(f"Repository : {data['repo_path']}")
            print(f"Is repo    : {data['is_repo']}")
            print(f"Branch     : {data['branch']}")
            print(f"Head       : {data['head']}")
            print(f"Dirty      : {data['dirty']}")
            if data["modified"]:
                print("Modified   : " + ", ".join(data["modified"]))
            if data["untracked"]:
                print("Untracked  : " + ", ".join(data["untracked"]))
        return 0

    # -- test --------------------------------------------------------------
    if args.command == "test":
        repo = args.repo or services.config.default_repo_path
        plan = services.tests.default_plan(repo, kind=args.kind, scope=args.scope)
        evidence = services.tests.run(plan, task_id="nao-cli")
        data = evidence.to_dict()
        if as_json:
            _print_json(data)
        else:
            print(f"Command : {' '.join(evidence.command)}")
            print(f"Status  : {evidence.status}")
            print(f"Exit    : {evidence.exit_code}")
            print(f"Passed  : {evidence.passed}")
            print(f"Failed  : {evidence.failed}")
            if evidence.stdout:
                print("--- stdout ---")
                print(evidence.stdout)
            if evidence.stderr:
                print("--- stderr ---")
                print(evidence.stderr)
        return 0 if evidence.ok else 1

    # -- audit -------------------------------------------------------------
    if args.command == "audit":
        events = manager.audit_events(args.task_id)
        if as_json:
            _print_json(events)
        else:
            for event in events:
                print(json.dumps(event, ensure_ascii=False))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
