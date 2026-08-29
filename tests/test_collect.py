"""The run loop a multi-object connector gets for free.

The contract `brain sync` depends on: --probe writes nothing, one object
failing does not cost the others, and the exit code separates "this token
cannot read that" (normal) from "something is broken" (not).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brainiphy_cli import collect  # noqa: E402
from brainiphy_cli.httpclient import NoScope  # noqa: E402
from support import TempProjectTestCase, quiet  # noqa: E402


def record(rid: str, **extra):
    return {"id": rid, "title": f"Record {rid}", "body": "text", **extra}


class RunTests(TempProjectTestCase):
    def setUp(self):
        super().setUp()
        self.out = self.tmp / "out"

    def _run(self, collectors, *argv):
        with quiet() as buf:
            code = collect.run("crm", collectors, argv=["--out", str(self.out), *argv])
        return code, buf.getvalue()

    def test_records_are_written_under_their_collectors_subfolder(self):
        code, _ = self._run([
            collect.Collector("contacts", "contacts", lambda: [record("1"), record("2")]),
            collect.Collector("deals", "deals", lambda: [record("9")]),
        ])
        self.assertEqual(code, 0)
        self.assertEqual(len(list((self.out / "contacts").glob("*.md"))), 2)
        self.assertEqual(len(list((self.out / "deals").glob("*.md"))), 1)

    def test_probe_reports_without_writing_anything(self):
        # The first thing you want from a new connector: what can this
        # credential actually read? Scope discovery has to be empirical.
        code, printed = self._run(
            [collect.Collector("contacts", "contacts", lambda: [record("1")])], "--probe")
        self.assertEqual(code, 0)
        self.assertFalse(self.out.exists())
        self.assertIn("probe", printed)
        self.assertIn("contacts", printed)

    def test_a_missing_scope_costs_that_object_and_nothing_else(self):
        def denied():
            raise NoScope("HTTP 403: insufficient scope")

        code, printed = self._run([
            collect.Collector("contacts", "contacts", denied),
            collect.Collector("deals", "deals", lambda: [record("9")]),
        ])
        # Exit 0: a partial token is the normal shape of a vendor credential,
        # not a failed sync.
        self.assertEqual(code, 0)
        self.assertIn("no scope", printed)
        self.assertEqual(len(list((self.out / "deals").glob("*.md"))), 1)

    def test_a_real_failure_is_isolated_but_still_exits_non_zero(self):
        def broken():
            raise ValueError("the vendor changed the response shape")

        code, printed = self._run([
            collect.Collector("contacts", "contacts", broken),
            collect.Collector("deals", "deals", lambda: [record("9")]),
        ])
        self.assertEqual(code, 1)
        self.assertIn("ValueError", printed)
        # The healthy object still ran — one bad object must not sink the rest.
        self.assertEqual(len(list((self.out / "deals").glob("*.md"))), 1)

    def test_only_runs_just_the_named_collectors(self):
        code, _ = self._run([
            collect.Collector("contacts", "contacts", lambda: [record("1")]),
            collect.Collector("deals", "deals", lambda: [record("9")]),
        ], "--only", "deals")
        self.assertEqual(code, 0)
        self.assertFalse((self.out / "contacts").exists())
        self.assertTrue((self.out / "deals").exists())

    def test_an_unknown_collector_name_is_refused_rather_than_silently_empty(self):
        # Otherwise a typo in --only reads as "that object has no records".
        code, printed = self._run(
            [collect.Collector("contacts", "contacts", lambda: [record("1")])],
            "--only", "contancts")
        self.assertEqual(code, 2)
        self.assertIn("contancts", printed)

    def test_collectors_run_in_declaration_order(self):
        # Order is load-bearing: a collector may fill a lookup the next one
        # reads, so a stage renders as a name rather than a uuid.
        order = []
        self._run([
            collect.Collector("pipelines", "pipelines", lambda: order.append("pipelines") or []),
            collect.Collector("opportunities", "opportunities",
                              lambda: order.append("opportunities") or []),
        ])
        self.assertEqual(order, ["pipelines", "opportunities"])

    def test_extra_record_fields_become_frontmatter(self):
        import yaml

        self._run([collect.Collector(
            "deals", "deals", lambda: [record("1", stage="Awaiting payment", value=500)])])
        written = next((self.out / "deals").glob("*.md"))
        _, frontmatter, _ = written.read_text().split("---", 2)
        data = yaml.safe_load(frontmatter)
        self.assertEqual(data["stage"], "Awaiting payment")
        self.assertEqual(data["source_system"], "crm")

    def test_re_running_overwrites_rather_than_duplicating(self):
        collectors = [collect.Collector("contacts", "contacts", lambda: [record("1")])]
        self._run(collectors)
        self._run(collectors)
        self.assertEqual(len(list((self.out / "contacts").glob("*.md"))), 1)


class KvBlockTests(unittest.TestCase):
    def test_empty_values_are_left_out(self):
        # A record body is read by a model building the graph; empty labels are
        # noise that costs tokens and says nothing.
        text = collect.kv_block({"a": "1", "b": "", "c": None, "d": []}, ["a", "b", "c", "d"])
        self.assertEqual(text, "- **a**: 1")

    def test_structured_values_are_rendered_as_json(self):
        text = collect.kv_block({"tags": ["x", "y"]}, ["tags"])
        self.assertIn('["x", "y"]', text)

    def test_only_the_requested_keys_appear(self):
        text = collect.kv_block({"a": "1", "secret": "shh"}, ["a"])
        self.assertNotIn("shh", text)


if __name__ == "__main__":
    unittest.main()
