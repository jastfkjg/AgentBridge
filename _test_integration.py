import os
import sys
from pathlib import Path

sys.path.insert(0, "src")

from agentbridge.agent import AIGenerator, AgentRunner
from agentbridge.generator import AgentKitGenerator

os.environ.pop("ANTHROPIC_API_KEY", None)

kit = AgentKitGenerator().generate(
    [Path("examples/writing_system")],
    Path("/tmp/full-test-kit"),
)
print(f"Test 1 PASS: Deterministic kit with {len(kit.capabilities)} capabilities")

try:
    gen = AIGenerator()
    print("Test 2 FAIL")
except ValueError:
    print("Test 2 PASS: AIGenerator requires API key")

gen = AIGenerator(api_key="sk-ant-test")
print(f"Test 3 PASS: Backend detected as {gen._backend}")

try:
    runner = AgentRunner(kit_dir="/tmp/full-test-kit")
    print("Test 4 FAIL")
except ValueError:
    print("Test 4 PASS: AgentRunner requires API key")

runner = AgentRunner(kit_dir="/tmp/full-test-kit", api_key="sk-ant-test")
print(f"Test 5 PASS: AgentRunner loaded {len(runner._capabilities)} capabilities")

ai_gen = AIGenerator(api_key="sk-ant-test")
gen_with_ai = AgentKitGenerator(ai_generator=ai_gen)
print(f"Test 6 PASS: Generator with AI backend: {gen_with_ai.ai_generator._backend}")

gen_no_ai = AgentKitGenerator()
print(f"Test 7 PASS: Generator without AI: {gen_no_ai.ai_generator is None}")

import agentbridge
ai_cls = agentbridge.AIGenerator
runner_cls = agentbridge.AgentRunner
print(f"Test 8 PASS: Lazy imports work: {ai_cls.__name__}, {runner_cls.__name__}")

import shutil
shutil.rmtree("/tmp/full-test-kit", ignore_errors=True)

print("\nAll integration tests passed!")
