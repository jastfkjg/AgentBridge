import unittest
from pathlib import Path

from agentbridge.discovery import CapabilityDiscoverer


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "writing_system"


class DiscoveryTests(unittest.TestCase):
    def test_discovers_openapi_graphql_sql_and_routes(self):
        capabilities = CapabilityDiscoverer().discover([EXAMPLE])
        names = {cap.name for cap in capabilities}

        self.assertIn("create_chapter", names)
        self.assertIn("delete_character", names)
        self.assertIn("publish_project", names)
        self.assertIn("send_email", names)
        self.assertIn("create_scene", names)

    def test_high_risk_capabilities_require_confirmation(self):
        capabilities = CapabilityDiscoverer().discover([EXAMPLE])
        high_risk = [cap for cap in capabilities if cap.risk in {"destructive", "external_side_effect"}]

        self.assertTrue(high_risk)
        self.assertTrue(all(cap.confirm_required for cap in high_risk))


if __name__ == "__main__":
    unittest.main()

