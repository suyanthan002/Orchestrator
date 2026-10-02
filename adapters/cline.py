from orchestrator.agent import AgentTask
from orchestrator.parser import parse_cline_output
from orchestrator.runner import AgentRunner


class ClineAdapter:

    name = "cline"

    def __init__(self, runner: AgentRunner):
        self.runner = runner

    def execute(self, task: AgentTask):

        command = [
            r"C:\Program Files\nodejs\node.exe",
            r"C:\Users\Admin\AppData\Roaming\npm\node_modules\cline\bin\cline",
            "--json",
            "--plan",
            "--auto-approve=true",
        ]

        result = self.runner.run(
            agent_name=self.name,
            command=command,
            task=task,
        )

        parsed = parse_cline_output(
            result.stdout
        )

        result.success = (
            result.success
            and parsed.success
        )

        result.final_response = (
            parsed.final_response
        )

        result.finish_reason = (
            parsed.finish_reason
        )

        result.iterations = (
            parsed.iterations
        )

        result.usage = (
            parsed.usage
        )

        return result