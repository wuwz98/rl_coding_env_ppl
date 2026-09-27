import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from run_agent import (
    ModelConfig,
    build_prompt,
    function_calls,
    load_model_config,
    parse_assignment,
    response_text,
    truncate_middle,
    usage_totals,
)


class ConfigTests(unittest.TestCase):
    def test_parse_assignment_supports_quoted_and_unquoted_values(self):
        text = """
API_KEY=secret-value
model='endpoint-name'
base_url="https://example.test/api/v3"
"""
        self.assertEqual(parse_assignment(text, "API_KEY"), "secret-value")
        self.assertEqual(parse_assignment(text, "model"), "endpoint-name")
        self.assertEqual(
            parse_assignment(text, "base_url"),
            "https://example.test/api/v3",
        )

    def test_environment_overrides_endpoint_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            endpoint = Path(temporary) / "endpoint.md"
            endpoint.write_text(
                "API_KEY=file-key\n"
                "model='file-model'\n"
                "base_url='https://file.test/v3'\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "ARK_API_KEY": "env-key",
                    "ARK_MODEL": "env-model",
                    "ARK_BASE_URL": "https://env.test/v3/",
                },
                clear=False,
            ):
                config = load_model_config(endpoint)
        self.assertEqual(config.api_key, "env-key")
        self.assertEqual(config.model, "env-model")
        self.assertEqual(config.base_url, "https://env.test/v3")

    def test_model_config_repr_does_not_leak_key(self):
        config = ModelConfig(
            api_key="top-secret",
            model="model",
            base_url="https://example.test",
        )
        self.assertNotIn("top-secret", repr(config))


class ProtocolTests(unittest.TestCase):
    def test_extracts_function_calls_and_message_text(self):
        output = [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Inspecting."}],
            },
            {
                "type": "function_call",
                "name": "bash",
                "call_id": "call-1",
                "arguments": '{"command":"pwd"}',
            },
        ]
        self.assertEqual(function_calls(output), [output[1]])
        self.assertEqual(response_text(output), "Inspecting.")

    def test_truncates_middle_and_keeps_both_ends(self):
        value = "A" * 100 + "B" * 100
        truncated = truncate_middle(value, 80)
        self.assertLessEqual(len(truncated), 80)
        self.assertTrue(truncated.startswith("A"))
        self.assertTrue(truncated.endswith("B"))
        self.assertIn("truncated", truncated)

    def test_sums_usage_across_model_events(self):
        events = [
            {
                "kind": "model",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                },
            },
            {"kind": "tool", "outcome": {}},
            {
                "kind": "model",
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 3,
                    "total_tokens": 23,
                },
            },
        ]
        self.assertEqual(
            usage_totals(events),
            {
                "input_tokens": 30,
                "output_tokens": 5,
                "total_tokens": 35,
            },
        )

    def test_prompt_contains_public_task_but_no_private_assets(self):
        prompt = build_prompt(
            {
                "workspace": "/workspace/repo",
                "problem_statement": "Fix the widget behavior.",
            }
        )
        self.assertIn("/workspace/repo", prompt)
        self.assertIn("Fix the widget behavior.", prompt)
        self.assertNotIn("gold.patch", prompt)
        self.assertNotIn("test.patch", prompt)


if __name__ == "__main__":
    unittest.main()
