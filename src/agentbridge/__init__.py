"""AgentBridge generates Agent Integration Kits for existing systems using LLM-powered generation."""

from agentbridge.agent import AIGenerator
from agentbridge.generator import AgentKitGenerator
from agentbridge.models import Capability, IntegrationKit

__all__ = ["AIGenerator", "AgentKitGenerator", "Capability", "IntegrationKit"]


def __getattr__(name: str):
    if name == "AgentRunner":
        from agentbridge.agent import AgentRunner
        return AgentRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
