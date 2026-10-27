"""Kilo Code adapter.

Kilo exposes both a CLI and ACP. NAO uses the CLI NDJSON interface here; the
permission/command shaping is configuration-driven so the adapter stays free of
hard-coded flags.
"""

from __future__ import annotations

from ..models.enums import Permission
from .base import BaseCliAdapter


class KiloAdapter(BaseCliAdapter):
    name = "kilo"
    # Kilo's JSON mode emits run/result style events like Cline's.
    result_event_types = ("run_result", "result")

    def enforceable_permissions(self) -> frozenset[Permission]:
        return frozenset(
            {
                Permission.READ,
                Permission.SEARCH,
                Permission.GIT_READ,
                Permission.WRITE,
                Permission.SHELL,
            }
        )
