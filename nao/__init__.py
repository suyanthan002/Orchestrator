"""NAO - Nexora Agent Orchestrator.

NAO is a controlled multi-agent software-engineering orchestration platform.
It receives a task, plans the work, selects appropriate agents, gives each
agent isolated context and permissions, coordinates implementation and
verification, manages Git safely, and returns an auditable final outcome.

The modules in this package implement the architecture described in
``doc/plan.md``. The legacy Cline prototype (the ``orchestrator`` package and
the top-level ``adapters`` package) is preserved and reused where sensible.
"""

__version__ = "1.0.0"
