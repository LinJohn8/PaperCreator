"""Multi-agent writing subsystem.

Eleven narrow LLM roles composed into four pipelines. Roles communicate only
through a :class:`~papercreator.agents.base.Blackboard`, which is what makes any
single step independently re-runnable.

Public surface::

    from papercreator.agents import orchestrator, roles

    orchestrator.submit_run(project_id=..., pipeline="full_auto")
    orchestrator.describe_pipelines()
    roles.describe_roles()

Pipelines: ``full_auto`` (idea -> finished draft), ``section`` (draft/redraft
chosen sections), ``stitch`` (join independently written parts), ``custom``
(explicit role list, used by skills).

See ``docs/systems/agent_system.md``.
"""

from . import orchestrator, prompts, roles  # noqa: F401
from .base import Agent, AgentResult, Blackboard, RunConfig  # noqa: F401

__all__ = [
    "Agent",
    "AgentResult",
    "Blackboard",
    "RunConfig",
    "orchestrator",
    "prompts",
    "roles",
]
