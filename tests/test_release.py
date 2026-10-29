import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_public_defaults_are_empty_and_presentation_is_generic(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["api_url"]["default"], "")
        self.assertEqual(schema["page_url"]["default"], "")
        self.assertEqual(schema["group_ids"]["default"], [])
        self.assertEqual(schema["target_school"]["default"], "")
        self.assertEqual(schema["platform_id"]["default"], "")
        self.assertEqual(schema["event_name"]["default"], "赛事")
        self.assertIn("LINK-1", schema["api_url"]["description"])
        self.assertIn("LINK-2", schema["page_url"]["description"])


if __name__ == "__main__":
    unittest.main()
