"""Tests for receipt.py on made-up transcripts. Run: python3 test_receipt.py"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "receipt.py")
spec = importlib.util.spec_from_file_location("receipt", SCRIPT)
receipt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receipt)

NOW = datetime.now(timezone.utc)


def ts(days_ago=0.0):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def line(mid, model, usage, days_ago=0.0, rid=None):
    return json.dumps({
        "type": "assistant", "timestamp": ts(days_ago), "requestId": rid or "req_" + mid,
        "message": {"id": mid, "model": model, "role": "assistant", "usage": usage, "content": []},
    })


def u(inp=0, out=0, read=0, w5=0, w1=0, **extra):
    usage = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
             "cache_creation_input_tokens": w5 + w1,
             "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}}
    usage.update(extra)
    return usage


def build_home(root):
    project = os.path.join(root, ".claude", "projects", "-Users-someone-code-shop")
    os.makedirs(os.path.join(project, "session-a", "subagents"))
    fallback = u(64, 8769, 402195, 0, 4604)
    fallback["iterations"] = [
        dict(u(32, 1455, 243776, 0, 4604), type="message", model="claude-fable-5-1"),
        dict(u(32, 7314, 158419), type="fallback_message", model="claude-opus-4-8"),
    ]
    no_split = {"input_tokens": 0, "output_tokens": 100, "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 1000}
    session_a = [
        # Same response on three lines; only the last has the final output count.
        line("m1", "claude-opus-5-5", u(10, 3, 100000, 2000)),
        line("m1", "claude-opus-5-5", u(10, 3, 100000, 2000)),
        line("m1", "claude-opus-5-5", u(10, 1000, 100000, 2000)),
        line("m2", "claude-fable-5-2", u(0, 2000, 1000000, 0, 10000, speed="fast")),
        line("m3", "claude-haiku-5-5", u(1000, 500, 150000)),  # prompt over 100K: higher Haiku 5.5 tier
        line("m4", "claude-opus-5-5", u(100, 0, server_tool_use={"web_search_requests": 3})),
        line("m5", "claude-opus-4-8", fallback),
        line("m6", "claude-sonnet-4-5-20250929", no_split),
        line("m7", "claude-opus-5-5", u(0, 1000, inference_geo="us")),
        line("s1", "<synthetic>", u(0, 0)),
        line("x1", "claude-future-9", u(0, 1000)),
        line("old", "claude-sonnet-5-5", u(0, 1000000), days_ago=100),
        "not json at all",
    ]
    with open(os.path.join(project, "session-a.jsonl"), "w") as f:
        f.write("\n".join(session_a) + "\n")
    # A resumed session repeats m1 in a new file.
    with open(os.path.join(project, "session-b.jsonl"), "w") as f:
        f.write(line("m1", "claude-opus-5-5", u(10, 1000, 100000, 2000)) + "\n")
    with open(os.path.join(project, "session-a", "subagents", "agent-1.jsonl"), "w") as f:
        f.write(line("m8", "claude-opus-5-5", u(0, 500)) + "\n")


# Hand-computed costs, $ (prices per million tokens in receipt.PRICES).
EXPECTED_30D = (
    10 * 4e-6 + 1000 * 20e-6 + 100000 * 0.2e-6 + 2000 * 4e-6 * 1.25      # m1 opus-5-5
    + (2000 * 40e-6 + 1000000 * 0.2e-6 + 10000 * 8e-6 * 2) * 2         # m2 fable-5-2, fast
    + 1000 * 0.5e-6 + 500 * 2.5e-6 + 150000 * 0.05e-6                  # m3 haiku-5-5, long prompt
    + 100 * 4e-6 + 3 * 0.01                                            # m4 opus-5-5 + 3 web searches
    + 32 * 10e-6 + 1455 * 50e-6 + 243776 * 0.25e-6 + 4604 * 10e-6 * 2  # m5 fable-5-1 part
    + 32 * 5e-6 + 7314 * 25e-6 + 158419 * 0.5e-6                       # m5 opus-4-8 part
    + 100 * 15e-6 + 1000 * 3e-6 * 1.25                                 # m6 sonnet-4-5, no TTL split
    + 1000 * 20e-6 * 1.1                                               # m7 opus-5-5, US-only
    + 500 * 20e-6                                                      # m8 opus-5-5 subagent
)
EXPECTED_ALL = EXPECTED_30D + 1000000 * 10e-6                          # old sonnet-5-5


def run(home, *args):
    env = dict(os.environ, HOME=home)
    env.pop("CLAUDE_CONFIG_DIR", None)
    p = subprocess.run([sys.executable, "-I", SCRIPT] + list(args), env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return p.returncode, p.stdout


class ReceiptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = cls.tmp.name
        build_home(cls.home)
        cls.files = receipt.transcript_files([os.path.join(cls.home, ".claude", "projects")])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def totals(self, days=None):
        cutoff = NOW - timedelta(days=days) if days else None
        r, seen = receipt.Receipt(), set()
        for path in self.files:
            for key, (_, model, when, usage) in receipt.read_file(path, cutoff).items():
                if key not in seen:
                    seen.add(key)
                    r.add(model, when, usage)
        return r

    def test_cost_matches_hand_math(self):
        self.assertAlmostEqual(self.totals(30).total_cost(), EXPECTED_30D, places=9)
        self.assertAlmostEqual(self.totals().total_cost(), EXPECTED_ALL, places=9)

    def test_counts_each_response_once(self):
        r = self.totals(30)
        self.assertEqual(r.requests, 8)
        self.assertEqual(r.tokens["output"], 1000 + 2000 + 500 + 1455 + 7314 + 100 + 1000 + 500)
        self.assertEqual(r.tokens["cache_read"], 100000 + 1000000 + 150000 + 243776 + 158419)
        self.assertEqual(r.tokens["cache_write"], 2000 + 10000 + 4604 + 1000)
        self.assertEqual(r.tokens["input"], 10 + 1000 + 100 + 64)
        self.assertEqual(r.searches, 3)
        self.assertEqual(r.unpriced, {"claude-future-9": 1})

    def test_model_names(self):
        self.assertEqual(receipt.model_key("claude-opus-4-5-20251101"), "claude-opus-4-5")
        self.assertEqual(receipt.model_key("us.anthropic.claude-opus-4-5-20251101-v1:0"), "claude-opus-4-5")
        self.assertEqual(receipt.model_key("claude-opus-4-1@20250805"), "claude-opus-4-1")
        self.assertEqual(receipt.model_key("claude-sonnet-4-5[1m]"), "claude-sonnet-4-5")
        self.assertEqual(receipt.model_key("claude-fable-5-2"), "claude-fable-5-2")
        self.assertNotIn("claude-fable-5-3", receipt.PRICES)  # a new model is reported, not priced as an old one

    def test_printed_receipt(self):
        code, out = run(self.home, "--days", "30")
        self.assertEqual(code, 0)
        self.assertIn("Total       %s" % receipt.money(EXPECTED_30D), out)
        self.assertIn("Requests    8", out)
        self.assertIn("98% of your tokens were the model re-reading cached context.", out)
        self.assertIn("claude-future-9 (1 request)", out)
        self.assertIn("Sent nothing anywhere.", out)

    def test_empty_home(self):
        with tempfile.TemporaryDirectory() as empty:
            code, out = run(empty)
        self.assertEqual(code, 1)
        self.assertIn("No Claude Code transcripts found, so there is nothing to read.", out)

    def test_nothing_in_window(self):
        with tempfile.TemporaryDirectory() as home:
            project = os.path.join(home, ".claude", "projects", "p")
            os.makedirs(project)
            with open(os.path.join(project, "s.jsonl"), "w") as f:
                f.write(line("a", "claude-opus-5-5", u(0, 10), days_ago=60) + "\n")
            code, out = run(home, "--days", "7")
        self.assertEqual(code, 1)
        self.assertIn("found no Claude Code requests in the last 7 days", out)


if __name__ == "__main__":
    unittest.main()
