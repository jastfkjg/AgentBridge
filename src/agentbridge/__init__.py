"""AgentBridge parses existing systems into Claude-controllable Agent Integration Kits."""

from agentbridge.agent import AIGenerator
from agentbridge.generator import AgentKitGenerator
from agentbridge.models import Capability, IntegrationKit

__all__ = ["AIGenerator", "AgentKitGenerator", "Capability", "IntegrationKit"]


def __getattr__(name: str):
    if name == "AgentRunner":
        from agentbridge.agent import AgentRunner
        return AgentRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
