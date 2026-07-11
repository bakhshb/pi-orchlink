"""Per-group Typer command modules for the ``orch`` CLI.

``cli.main`` owns app wiring; modules in this package register command groups on
that shared Typer app.
"""

from __future__ import annotations

import typer

from orchlink.cli.commands import broker, diagnose, init, jobs, lead, talk, tasks, update

ALL_MODULES = (init, lead, talk, tasks, jobs, diagnose, update, broker)


def register_all(app: typer.Typer) -> None:
    """Register every command group's commands on ``app`` in their original order."""
    init.register_init(app)
    lead.register_lead(app)
    tasks.register_send(app)
    jobs.register_jobs(app)
    talk.register_talk(app)
    diagnose.register_diagnose(app)
    update.register_update(app)
    broker.register_broker(app)


__all__ = [
    "ALL_MODULES",
    "broker",
    "diagnose",
    "init",
    "jobs",
    "lead",
    "register_all",
    "talk",
    "tasks",
    "update",
]
