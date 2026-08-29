"""The list of brains, and the promise it makes about not touching them.

The one rule worth testing hardest: forgetting a brain removes a line from a
YAML file and nothing else. `brain forget` and the `d` key in the app sit right
next to navigation keys, and the fear they have to answer — "have I just
deleted a client's knowledge base?" — is only answerable by the code actually
never doing that.

Everything shown about a brain is read live from the brain itself; nothing here
is cached, so these tests also pin that a stale summary is impossible.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brainiphy_cli import brains, project as project_mod  # noqa: E402
from support import TempProjectTestCase, quiet  # noqa: E402


class BrainsTestCase(TempProjectTestCase):
    """The registry is redirected into the temp dir by TempProjectTestCase, so
    nothing here can reach the real list of brains."""


class RegistryLocationTests(BrainsTestCase):
    def test_the_home_can_be_pointed_elsewhere(self):
        # Which is what keeps a test run out of the developer's own list.
        self.assertEqual(brains.home(), self.tmp / "config")
        self.assertEqual(brains.registry_path(), self.tmp / "config" / "brains.yaml")

    def test_no_registry_yet_reads_as_no_brains(self):
        self.assertEqual(brains.known(), [])

    def test_a_corrupt_registry_does_not_break_every_command(self):
        # The list is shown by several screens; a file someone hand-edited
        # badly should cost the list, not the tool.
        path = brains.registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("brains: [ unclosed", encoding="utf-8")
        self.assertEqual(brains.known(), [])


class RememberForgetTests(BrainsTestCase):
    def test_a_brain_is_remembered_once(self):
        self.scaffold()
        # scaffold() already registered it, so this is the second attempt.
        self.assertFalse(brains.remember(self.project))
        self.assertEqual(brains.known(), [self.project.resolve()])

    def test_scaffolding_a_brain_puts_it_on_the_list(self):
        # The list is only useful if it is complete, and asking someone to
        # register a brain they just made would be a second step for one
        # intention.
        self.assertEqual(brains.known(), [])
        self.scaffold()
        self.assertIn(self.project.resolve(), brains.known())

    def test_order_is_the_order_they_were_added(self):
        first, second = self.tmp / "a", self.tmp / "b"
        brains.remember(first)
        brains.remember(second)
        self.assertEqual(brains.known(), [first.resolve(), second.resolve()])

    def test_forgetting_removes_the_entry_and_nothing_else(self):
        """The promise the whole feature rests on."""
        self.scaffold()
        self.write_connector("crm", "# real work\n")
        marker = self.project / "raw" / "docs" / "note.md"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("a mirrored document\n", encoding="utf-8")

        self.assertTrue(brains.forget(self.project))

        self.assertEqual(brains.known(), [])
        # Every one of these would be a catastrophe to lose.
        self.assertTrue(self.project.is_dir())
        self.assertTrue((self.project / "connectors" / "registry.yaml").exists())
        self.assertTrue((self.project / "connectors" / "crm" / "sync.py").exists())
        self.assertEqual(marker.read_text(), "a mirrored document\n")

    def test_forgetting_something_that_is_not_listed_says_so(self):
        self.assertFalse(brains.forget(self.tmp / "never-added"))

    def test_a_forgotten_brain_can_be_added_back(self):
        self.scaffold()
        brains.forget(self.project)
        self.assertTrue(brains.remember(self.project))
        self.assertEqual(brains.known(), [self.project.resolve()])


class SummaryTests(BrainsTestCase):
    def test_a_folder_that_is_gone_is_reported_missing_not_dropped(self):
        # A brain disappearing from the list on its own is indistinguishable
        # from a bug, so it stays and says what happened.
        brains.remember(self.tmp / "moved-away")
        summary = brains.summaries()[0]
        self.assertTrue(summary.missing)
        self.assertEqual(len(brains.known()), 1)

    def test_a_folder_that_is_not_a_brain_is_also_missing(self):
        plain = self.tmp / "just-a-folder"
        plain.mkdir()
        brains.remember(plain)
        self.assertTrue(brains.summaries()[0].missing)

    def test_a_fresh_brain_reports_what_it_still_needs(self):
        self.scaffold()
        summary = brains.summarize(self.project)
        self.assertFalse(summary.missing)
        self.assertEqual(summary.sources, 0)
        self.assertIsNone(summary.nodes)
        self.assertFalse(summary.complete)

    def test_sources_are_split_into_ready_and_unfinished(self):
        # "3 sources" is not the useful number when one of them cannot run.
        self.scaffold()
        self.write_connector("docs", "MIRROR_SOURCE = '/x'\n")
        self.write_connector("crm", "def fetch():\n    raise NotImplementedError\n")
        summary = brains.summarize(self.project)
        self.assertEqual(summary.sources, 2)
        self.assertEqual(summary.sources_ready, 1)
        self.assertEqual(summary.pending_sources, 1)

    def test_the_graph_size_is_read_from_the_graph(self):
        self.scaffold()
        self.write_graph(nodes=12, edges=8)
        self.assertEqual(brains.summarize(self.project).nodes, 12)

    def test_the_last_sync_comes_from_the_connector_state(self):
        import json

        self.scaffold()
        state = self.project / "connectors" / "state" / "docs.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        when = datetime.now(timezone.utc) - timedelta(hours=3)
        state.write_text(json.dumps({"last_run": when.isoformat()}), encoding="utf-8")
        summary = brains.summarize(self.project)
        self.assertIsNotNone(summary.last_sync)
        self.assertEqual(brains.ago(summary.last_sync), "3h ago")

    def test_a_bad_state_file_does_not_break_the_list(self):
        self.scaffold()
        state = self.project / "connectors" / "state" / "docs.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("not json", encoding="utf-8")
        self.assertIsNone(brains.summarize(self.project).last_sync)


class AgoTests(unittest.TestCase):
    def test_never_synced(self):
        self.assertEqual(brains.ago(None), "never")

    def test_relative_wording(self):
        now = datetime.now(timezone.utc)
        cases = [
            (timedelta(seconds=10), "just now"),
            (timedelta(minutes=5), "5m ago"),
            (timedelta(hours=2), "2h ago"),
            (timedelta(days=4), "4d ago"),
            (timedelta(days=40), "40d ago"),
        ]
        for delta, expected in cases:
            with self.subTest(delta=delta):
                self.assertEqual(brains.ago(now - delta), expected)


class VisualizerTests(BrainsTestCase):
    """graphify extract writes graph.json and does not redraw graph.html, so
    the picture is quietly the previous one until something notices."""

    def setUp(self):
        super().setUp()
        self.scaffold()
        self.out = self.project / "graphify-out"
        self.out.mkdir(parents=True, exist_ok=True)

    def _touch(self, name: str, mtime: float) -> Path:
        path = self.out / name
        path.write_text("x", encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def test_no_graph_means_nothing_to_be_stale_about(self):
        self.assertFalse(brains.visualizer_is_stale(self.project))

    def test_a_graph_with_no_picture_is_stale(self):
        self._touch("graph.json", 1000)
        self.assertTrue(brains.visualizer_is_stale(self.project))

    def test_a_picture_older_than_the_graph_is_stale(self):
        self._touch("graph.html", 1000)
        self._touch("graph.json", 2000)
        self.assertTrue(brains.visualizer_is_stale(self.project))

    def test_a_picture_newer_than_the_graph_is_current(self):
        self._touch("graph.json", 1000)
        self._touch("graph.html", 2000)
        self.assertFalse(brains.visualizer_is_stale(self.project))


class OpenGraphTests(BrainsTestCase):
    def setUp(self):
        super().setUp()
        self.scaffold()

    def test_a_brain_with_no_graph_says_how_to_build_one(self):
        with quiet() as buf:
            self.assertFalse(project_mod.open_graph(self.project))
        self.assertIn("brain sync", buf.getvalue())

    def test_a_current_picture_is_opened_without_redrawing(self):
        out = self.project / "graphify-out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "graph.json").write_text("{}", encoding="utf-8")
        (out / "graph.html").write_text("<html>", encoding="utf-8")
        os.utime(out / "graph.json", (1000, 1000))
        os.utime(out / "graph.html", (2000, 2000))

        with mock.patch.object(project_mod, "webbrowser") as browser, \
                mock.patch.object(project_mod.subprocess, "run") as run, quiet():
            self.assertTrue(project_mod.open_graph(self.project))
        run.assert_not_called()
        browser.open.assert_called_once()

    def test_a_stale_picture_is_redrawn_without_asking_a_model(self):
        # cluster-only --no-label: opening the visualizer must never spend plan
        # usage or API credit.
        out = self.project / "graphify-out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "graph.json").write_text("{}", encoding="utf-8")
        (out / "graph.html").write_text("<html>", encoding="utf-8")
        os.utime(out / "graph.html", (1000, 1000))
        os.utime(out / "graph.json", (2000, 2000))

        with mock.patch.object(project_mod, "webbrowser"), \
                mock.patch.object(project_mod, "find_exe", return_value="/fake/graphify"), \
                mock.patch.object(project_mod.subprocess, "run",
                                  return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run, \
                quiet():
            project_mod.open_graph(self.project)
        argv = run.call_args[0][0]
        self.assertIn("cluster-only", argv)
        self.assertIn("--no-label", argv)


if __name__ == "__main__":
    unittest.main()
