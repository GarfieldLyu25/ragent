"""CLI dispatch regression tests, without online calls."""
import unittest

from eval.common.cli import build_parser


class CliTests(unittest.TestCase):
    def test_v2_forwards_its_own_flags(self):
        args = build_parser().parse_args(["v2", "run", "--mode", "agent", "--suite", "agentic", "--limit", "4"])
        self.assertEqual(args.v2_args, ["run", "--mode", "agent", "--suite", "agentic", "--limit", "4"])

    def test_original_rag_cli_is_retained(self):
        args = build_parser().parse_args(["rag", "run", "--limit", "2"])
        self.assertEqual(args.limit, 2)
        self.assertEqual(args.rag_command, "run")
