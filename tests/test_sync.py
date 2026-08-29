"""What `brain sync` decides before it shells out to anything.

Three decisions live in sync.py and every one of them fails *silently* when it
goes wrong, which is what makes them worth pinning:

  - which LLM backend indexes the documents (get it wrong and a user with a
    subscription is told to buy an API key, or a key they set is overridden);
  - which graphify command rebuilds the graph (`update` on a document corpus
    is a successful no-op, so the graph just stops being current);
  - whether a connector is due (get it wrong and a LaunchAgent either hammers
    an API every minute or never runs at all).
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brainiphy_cli import sync  # noqa: E402
from support import TempProjectTestCase, quiet  # noqa: E402

NO_ENV: dict[str, str] = {}


class ResolveBackendTests(unittest.TestCase):
    """The rule: explicit > configured key > Claude Code subscription."""

    def test_explicit_choice_wins_over_a_configured_key(self):
        # Someone with an Anthropic key who asks for ollama gets ollama. The
        # flag is the user overruling the detection, not a hint to it.
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=True):
            self.assertEqual(sync.resolve_backend("ollama"), "ollama")

    def test_a_configured_key_defers_to_graphify(self):
        # None means "pass no --backend and let graphify detect", which handles
        # custom providers and Azure endpoints better than we could here.
        for var in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "AWS_PROFILE"):
            with self.subTest(var=var), mock.patch.dict(os.environ, {var: "set"}, clear=True):
                self.assertIsNone(sync.resolve_backend())

    def test_falls_back_to_the_subscription_when_nothing_is_configured(self):
        # The whole point of the fallback: a first sync on a machine with
        # Claude Code installed indexes, instead of stopping at "no LLM key".
        with mock.patch.dict(os.environ, NO_ENV, clear=True), \
                mock.patch.object(sync.shutil, "which", return_value="/usr/local/bin/claude"):
            self.assertEqual(sync.resolve_backend(), "claude-cli")

    def test_no_key_and_no_claude_binary_stays_undecided(self):
        # Nothing to offer: graphify prints its own no-backend error, and
        # build_graph turns that into the actionable hint.
        with mock.patch.dict(os.environ, NO_ENV, clear=True), \
                mock.patch.object(sync.shutil, "which", return_value=None):
            self.assertIsNone(sync.resolve_backend())

    def test_env_var_list_still_covers_what_graphify_detects(self):
        """The documented maintenance hazard, asserted instead of hoped for.

        _BACKEND_ENV_VARS only gates the automatic fallback, so a variable
        graphify learns to detect and we do not means we override a backend the
        user configured. Skipped where graphify is not installed (CI installs
        only this package).
        """
        try:
            from graphify import llm
        except ImportError:
            self.skipTest("graphify not installed")

        detected = set()
        for cfg in llm.BACKENDS.values():
            if cfg.get("env_key"):
                detected.add(cfg["env_key"])
            detected.update(cfg.get("env_keys") or [])
        # Bedrock and ollama are detected by non-key variables, listed here
        # because graphify's BACKENDS table does not carry them.
        detected -= {"OLLAMA_API_KEY"}   # not part of detect_backend's path

        missing = detected - set(sync._BACKEND_ENV_VARS)
        self.assertEqual(missing, set(),
                         f"graphify detects {sorted(missing)}; add them to _BACKEND_ENV_VARS")


class BuildGraphCommandTests(TempProjectTestCase):
    """Which graphify command gets built, and with which flags."""

    def _run_build(self, *, full=False, backend=None, returncode=0, output="",
                   make_graph=True):
        """Call build_graph with the subprocess replaced, and return the argv
        it would have executed."""
        captured: dict[str, list[str]] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            if make_graph and returncode == 0:
                graph = self.project / "graphify-out" / "graph.json"
                graph.parent.mkdir(parents=True, exist_ok=True)
                graph.write_text('{"nodes": [], "links": []}', encoding="utf-8")
            return mock.Mock(returncode=returncode, stdout=output, stderr="")

        with mock.patch.object(sync, "find_graphify", return_value="/fake/bin/graphify"), \
                mock.patch.object(sync.subprocess, "run", side_effect=fake_run), \
                quiet() as buf:
            ok, label = sync.build_graph(self.project, full=full, backend=backend)
        return captured.get("cmd", []), ok, label, buf.getvalue()

    def test_first_build_is_a_full_extract_with_no_gitignore(self):
        # .gitignore lists raw/, and graphify honors it: without the flag the
        # very first build reports an empty project and nobody knows why.
        self.scaffold()
        cmd, ok, label, _ = self._run_build(backend="ollama")
        self.assertEqual(cmd[1], "extract")
        self.assertIn("--no-gitignore", cmd)
        self.assertTrue(ok)
        self.assertEqual(label, "graphify extract")

    def test_later_builds_are_incremental_updates(self):
        self.scaffold()
        self.write_graph()
        cmd, _, label, _ = self._run_build()
        self.assertEqual(cmd[1], "update")
        self.assertEqual(label, "graphify update")

    def test_update_gets_neither_backend_nor_no_gitignore(self):
        """`graphify update` takes --force and --no-cluster and nothing else.

        Both flags would be rejected outright, so an incremental sync would
        start failing everywhere. This is the guard against someone tidying up
        by hoisting the flags out of the `if`.
        """
        self.scaffold()
        self.write_graph()
        cmd, _, _, _ = self._run_build(backend="claude-cli")
        self.assertNotIn("--backend", cmd)
        self.assertNotIn("--no-gitignore", cmd)

    def test_full_forces_an_extract_even_with_a_graph_present(self):
        # The document corpus changed; only extract re-reads documents.
        self.scaffold()
        self.write_graph()
        cmd, _, _, _ = self._run_build(full=True, backend="ollama")
        self.assertEqual(cmd[1], "extract")

    def test_the_chosen_backend_reaches_the_command_line(self):
        self.scaffold()
        cmd, _, _, _ = self._run_build(backend="gemini")
        self.assertEqual(cmd[cmd.index("--backend") + 1], "gemini")

    def test_the_subscription_fallback_announces_itself(self):
        # It spends plan usage rather than API credit and runs one chunk at a
        # time; a user should not have to infer either from a slow sync.
        self.scaffold()
        with mock.patch.dict(os.environ, NO_ENV, clear=True), \
                mock.patch.object(sync.shutil, "which", return_value="/usr/local/bin/claude"):
            cmd, _, _, printed = self._run_build()
        self.assertEqual(cmd[cmd.index("--backend") + 1], "claude-cli")
        self.assertIn("subscription", printed)

    def test_a_zero_exit_without_a_graph_is_still_a_failure(self):
        # graphify can exit 0 having written nothing (an empty corpus). Calling
        # that a rebuild would leave `brain status` reporting a graph that is
        # not there.
        self.scaffold()
        _, ok, _, _ = self._run_build(make_graph=False)
        self.assertFalse(ok)

    def test_a_missing_backend_is_explained_rather_than_re_raised(self):
        self.scaffold()
        _, ok, _, printed = self._run_build(
            returncode=1, output="error: no LLM API key found", make_graph=False)
        self.assertFalse(ok)
        # The two free routes must both be offered, and the subscription first.
        self.assertIn("claude-cli", printed)
        self.assertIn("/graphify", printed)
        self.assertLess(printed.index("claude-cli"), printed.index("ANTHROPIC_API_KEY"))


class IsDueTests(TempProjectTestCase):
    """Interval enforcement across separate `brain sync` invocations."""

    def setUp(self):
        super().setUp()
        self.scaffold()

    def _state(self, name: str, payload: str) -> None:
        state = self.project / "connectors" / "state" / f"{name}.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(payload, encoding="utf-8")

    def test_a_connector_that_never_ran_is_due(self):
        self.assertTrue(sync.is_due(self.project, "crm", 60))

    def test_a_connector_that_just_ran_is_not(self):
        with quiet():
            sync._mark_ran(self.project, "crm")
        self.assertFalse(sync.is_due(self.project, "crm", 60))

    def test_a_connector_becomes_due_once_the_interval_passes(self):
        old = datetime.now(timezone.utc) - timedelta(minutes=90)
        self._state("crm", json.dumps({"last_run": old.isoformat()}))
        self.assertTrue(sync.is_due(self.project, "crm", 60))
        self.assertFalse(sync.is_due(self.project, "crm", 120))

    def test_an_unreadable_state_file_means_due(self):
        # Erring towards running again: a corrupt timestamp must not freeze a
        # connector forever, which is the failure nobody would think to look for.
        for payload in ("not json at all", "{}", '{"last_run": "yesterday"}'):
            with self.subTest(payload=payload):
                self._state("crm", payload)
                self.assertTrue(sync.is_due(self.project, "crm", 60))

    def test_a_naive_timestamp_means_due_rather_than_a_traceback(self):
        # Hand-edited or written by an older build: comparing a naive datetime
        # to an aware one raises TypeError, which used to escape and take the
        # whole sync down before any connector ran.
        self._state("crm", json.dumps({"last_run": "2026-01-01T00:00:00"}))
        self.assertTrue(sync.is_due(self.project, "crm", 60))


class RegistryTests(TempProjectTestCase):
    def test_missing_registry_reads_as_no_connectors(self):
        # `brain sync` on a folder that was never scaffolded must warn, not blow up.
        self.assertEqual(sync.load_registry(self.project), [])

    def test_scaffolded_registry_starts_empty(self):
        self.scaffold()
        self.assertEqual(sync.load_registry(self.project), [])

    def test_entries_round_trip(self):
        from brainiphy_cli import project as project_mod

        self.scaffold()
        project_mod.write_registry_entries(
            self.project, [{"name": "docs", "type": "local-folder", "interval_minutes": 5}])
        self.assertEqual(sync.load_registry(self.project)[0]["name"], "docs")

    def test_an_entry_without_a_name_is_reported_not_raised(self):
        # registry.yaml is a file users edit by hand; a missing key should cost
        # that connector, not the whole run.
        self.scaffold()
        (self.project / "connectors" / "registry.yaml").write_text(
            "connectors:\n- interval_minutes: 5\n- name: docs\n", encoding="utf-8")
        with quiet() as buf:
            report = sync.run(self.project, dry_run=False, full=False)
        self.assertTrue(any("name" in e for e in report.errors), buf.getvalue())


if __name__ == "__main__":
    unittest.main()
