import os
import sys

sys.path.insert(0, "src")

os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_BASE_URL", None)
os.environ.pop("ANTHROPIC_MODEL", None)

from agentbridge.agent import AIGenerator

gen = AIGenerator(
    api_key="sk-test-key",
    base_url="https://api.deepseek.com/anthropic",
    model="deepseek-v4-flash",
)
print(f"api_key: {gen.api_key[:6]}...")
print(f"base_url: {gen.base_url}")
print(f"model: {gen.model}")
print(f"backend: {gen._backend}")
assert gen._backend == "anthropic", "Custom endpoint should use anthropic backend"
assert gen.model == "deepseek-v4-flash"
assert gen.base_url == "https://api.deepseek.com/anthropic"
print("Custom provider config: PASS")

os.environ.pop("ANTHROPIC_BASE_URL", None)
os.environ.pop("ANTHROPIC_MODEL", None)
gen2 = AIGenerator(api_key="sk-test-key2")
print(f"default model: {gen2.model}")
print(f"default base_url: {gen2.base_url!r}")
assert gen2.model == "claude-sonnet-4-20250514"
assert gen2.base_url == ""
print("Default config: PASS")

os.environ["ANTHROPIC_MODEL"] = "my-custom-model"
os.environ["ANTHROPIC_BASE_URL"] = "https://custom.api/v1"
gen3 = AIGenerator(api_key="sk-test-key3")
print(f"env model: {gen3.model}")
print(f"env base_url: {gen3.base_url}")
assert gen3.model == "my-custom-model"
assert gen3.base_url == "https://custom.api/v1"
del os.environ["ANTHROPIC_MODEL"]
del os.environ["ANTHROPIC_BASE_URL"]
print("Env var override: PASS")

print("\nAll custom LLM provider tests passed!")
