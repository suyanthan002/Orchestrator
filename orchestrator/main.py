import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from orchestrator.agent import AgentTask
from orchestrator.runner import AgentRunner
from orchestrator.task_manager import TaskManager
from adapters.cline import ClineAdapter


ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "agents.json"
RUNS_DIR = ROOT / "runs"
LAB_FILE = ROOT / "lab" / "hello.py"


def load_config():
    return json.loads(
        CONFIG_FILE.read_text(
            encoding="utf-8"
        )
    )


def save_result(result):
    RUNS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = {
        "task_id": result.task_id,
        "agent": result.agent,
        "success": result.success,
        "exit_code": result.exit_code,
        "command": result.command,

        "finish_reason": result.finish_reason,
        "iterations": result.iterations,
        "usage": result.usage,
        "final_response": result.final_response,

        "stdout": result.stdout,
        "stderr": result.stderr,

        "completed_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    output_file = (
        RUNS_DIR
        / f"{result.task_id}.json"
    )

    output_file.write_text(
        json.dumps(
            output,
            indent=2,
        ),
        encoding="utf-8",
    )

    return output_file

def create_demo_task(config):
    cline_config = config["agents"]["cline"]

    task_manager = TaskManager(ROOT)

    prompt = f"""
You are being called by NAO
(Nexora Agent Orchestrator).

This is a READ-ONLY verification task.

Inspect this file:

{LAB_FILE}

Explain:

1. What the function does.
2. What happens when the file is executed directly.
3. What output it should produce.

STRICT RULES:

- Do NOT modify any file.
- Do NOT create any file.
- Do NOT delete any file.
- Do NOT run destructive commands.
- Return your findings to NAO.
"""

    return task_manager.create_task(
        prompt=prompt,
        working_directory=str(ROOT),
        timeout_seconds=cline_config["timeout_seconds"],
        task_type="read_only_verification",
        metadata={
            "target_file": str(LAB_FILE),
            "agent_mode": cline_config["mode"],
        },
    )

def main():

    parser = argparse.ArgumentParser(
        description=(
            "NAO - Nexora Agent "
            "Orchestrator"
        )
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Actually launch the "
            "selected agent."
        ),
    )

    args = parser.parse_args()

    config = load_config()

    cline_config = (
        config["agents"]["cline"]
    )

    task = create_demo_task(config)

    print()
    print("=" * 60)
    print("NAO v0.1")
    print("=" * 60)

    print(f"Task ID : {task.task_id}")
    print("Agent   : Cline")
    print("Mode    : READ-ONLY / PLAN")
    print(f"Target  : {LAB_FILE}")
    print()

    if not args.execute:
        print("DRY RUN")
        print("-" * 60)
        print("The task was created successfully.")
        print("Cline has NOT been launched.")
        print("No project files will be modified.")
        print()
        print("Run with --execute when ready for the controlled test.")
        print()
        return

    print("Launching Cline...")
    print()

    runner = AgentRunner()
    adapter = ClineAdapter(runner)
    result = adapter.execute(task)
    output_file = save_result(result)

    print("-" * 60)
    print(f"Success        : {result.success}")
    print(f"Exit code      : {result.exit_code}")
    print(f"Finish reason  : {result.finish_reason}")
    print(f"Iterations     : {result.iterations}")
    print(f"Result         : {output_file}")
    print("-" * 60)

    if result.final_response:
        print()
        print("Cline final response:")
        print("-" * 60)
        print(result.final_response)

    if result.usage:
        print()
        print("Usage:")
        print(json.dumps(
            result.usage,
            indent=2,
        ))

    if result.stderr:
        print()
        print("Cline stderr:")
        print("-" * 60)
        print(result.stderr)
if __name__ == "__main__":
    main()