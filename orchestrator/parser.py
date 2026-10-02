import json
from typing import Any, Optional


class ClineParseResult:
    def __init__(
        self,
        success: bool,
        final_response: Optional[str],
        finish_reason: Optional[str],
        iterations: Optional[int],
        usage: Optional[dict[str, Any]],
    ):
        self.success = success
        self.final_response = final_response
        self.finish_reason = finish_reason
        self.iterations = iterations
        self.usage = usage


def parse_cline_output(output: str) -> ClineParseResult:
    final_response = None
    finish_reason = None
    iterations = None
    usage = None

    for line in output.splitlines():
        line = line.strip()

        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") != "run_result":
            continue

        finish_reason = event.get("finishReason")
        final_response = event.get("text")
        iterations = event.get("iterations")
        usage = event.get("usage")

        break

    success = finish_reason == "completed"

    return ClineParseResult(
        success=success,
        final_response=final_response,
        finish_reason=finish_reason,
        iterations=iterations,
        usage=usage,
    )