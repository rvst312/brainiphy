"""The checklist's reading of a project on disk.

steps.inspect() is the one place that answers "how far along is this brain",
and three screens render it (`brain guide`, `brain status`, the app). Its
failure mode is not a crash: it is a checkbox that says done when it is not,
which sends a user to the next step with a connector that cannot run.

The detection is also deliberately *textual* — a half-written connector may not
import at all — so these tests write source files rather than modules.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brainiphy_cli import steps  # noqa: E402
from support import TempProjectTestCase, quiet  # noqa: E402

WORKING_CONNECTOR = '''\
SOURCE_SYSTEM = "crm"


def fetch_records():
    return []
'''

STUB_CONNECTOR = '''\
SOURCE_SYSTEM = "crm"


def fetch_records():
    raise NotImplementedError("write the fetching here")
'''

PRESET_MISSING_DETAILS = '''\
SOURCE_SYSTEM = "gohighlevel"
LOCATION_ID = "REPLACE_ME"
SECRET_ITEM = "graphify-acme-crm"
'''


def _step(state, key):
    return next(s for s in state.steps if s.key == key)


class StepDetectionTests(TempProjectTestCase):
    def test_an_empty_folder_has_nothing_done_but_step_one(self):
        # Step 1 is about the machine, not the project: graphify is either
        # installed or not, whatever folder you point at.
        state = steps.inspect(self.tmp / "not-a-brain")
        self.assertFalse(_step(state, "scaffold").done)
        self.assertFalse(_step(state, "sources").done)
        self.assertFalse(_step(state, "build").done)

    def test_scaffolding_settles_step_two(self):
        self.scaffold()
        state = steps.inspect(self.project)
        self.assertTrue(_step(state, "scaffold").done)
        self.assertFalse(_step(state, "sources").done)

    def test_a_registered_connector_settles_step_three(self):
        self.scaffold()
        self.write_connector("crm", WORKING_CONNECTOR)
        state = steps.inspect(self.project)
        self.assertTrue(_step(state, "sources").done)

    def test_a_built_graph_settles_step_five_and_reports_its_size(self):
        self.scaffold()
        self.write_graph(nodes=7, edges=4)
        state = steps.inspect(self.project)
        build = _step(state, "build")
        self.assertTrue(build.done)
        self.assertIn("7", build.detail)

    def test_an_unparseable_graph_does_not_count_as_built(self):
        # A truncated graph.json (an interrupted extract) must read as "not
        # built" rather than crashing every `brain status` in the project.
        self.scaffold()
        graph = self.project / "graphify-out" / "graph.json"
        graph.parent.mkdir(parents=True, exist_ok=True)
        graph.write_text("{ truncated", encoding="utf-8")
        self.assertFalse(_step(steps.inspect(self.project), "build").done)


class PendingConnectorTests(TempProjectTestCase):
    """Step 4 distinguishes two reasons a connector cannot run, and says which."""

    def setUp(self):
        super().setUp()
        self.scaffold()

    def test_a_stub_still_needs_code(self):
        self.write_connector("crm", STUB_CONNECTOR)
        state = steps.inspect(self.project)
        implement = _step(state, "implement")
        self.assertFalse(implement.done)
        self.assertIn("needs code", implement.detail)
        self.assertEqual(steps.unimplemented_connectors(self.project), ["crm"])

    def test_a_preset_still_needs_its_account_details(self):
        # A different problem with a different fix: the code is finished, the
        # account id is not. Saying "needs code" here sends the user to $EDITOR
        # to write something that is already written.
        self.write_connector("crm", PRESET_MISSING_DETAILS, type_="gohighlevel")
        implement = _step(steps.inspect(self.project), "implement")
        self.assertFalse(implement.done)
        self.assertIn("LOCATION_ID", implement.detail)

    def test_a_registered_connector_with_no_script_is_pending(self):
        from brainiphy_cli import project as project_mod

        project_mod.write_registry_entries(
            self.project, [{"name": "ghost", "type": "custom", "interval_minutes": 60}])
        self.assertEqual(steps.unimplemented_connectors(self.project), ["ghost"])

    def test_a_finished_connector_settles_step_four(self):
        self.write_connector("crm", WORKING_CONNECTOR)
        self.assertTrue(_step(steps.inspect(self.project), "implement").done)
        self.assertEqual(steps.unimplemented_connectors(self.project), [])

    def test_a_brain_of_only_mirrors_skips_step_four_without_claiming_credit(self):
        """SKIP is not DONE: it renders as a dash, but it must not hold up the
        next-step pointer either — a folder-only brain has nothing to implement."""
        state = steps.inspect(self.project)          # scaffolded, no connectors
        implement = _step(state, "implement")
        self.assertEqual(implement.state, steps.SKIP)
        self.assertTrue(implement.done)
        self.assertIsNot(state.next_step, implement)


class NextStepTests(TempProjectTestCase):
    def test_the_pointer_is_the_first_unsettled_step(self):
        self.scaffold()
        state = steps.inspect(self.project)
        with mock.patch.object(steps, "_find_exe", return_value="/usr/local/bin/graphify"):
            state = steps.inspect(self.project)
        self.assertEqual(state.next_step.key, "sources")

    def test_a_finished_brain_has_no_next_step(self):
        self.scaffold()
        self.write_connector("crm", WORKING_CONNECTOR)
        self.write_graph()
        (self.project / "CLAUDE.md").write_text("uses graphify\n", encoding="utf-8")
        with mock.patch.object(steps, "_find_exe", return_value="/usr/local/bin/graphify"), \
                mock.patch.object(steps, "_launch_agent", return_value=Path("/fake.plist")):
            state = steps.inspect(self.project)
        self.assertIsNone(state.next_step, [s.key for s in state.steps if not s.done])
        self.assertTrue(state.complete)


class RenderTests(TempProjectTestCase):
    """The screens must survive every state, including the empty one — they are
    the first thing a new user sees."""

    def test_rendering_an_empty_project_does_not_raise(self):
        with quiet() as buf:
            steps.render(steps.inspect(self.tmp / "nothing-here"))
        self.assertIn("Install graphify", buf.getvalue())

    def test_rendering_a_half_built_brain_does_not_raise(self):
        self.scaffold()
        self.write_connector("crm", STUB_CONNECTOR)
        with quiet() as buf:
            steps.render(steps.inspect(self.project), verbose=True)
            steps.render_status(self.project)
        self.assertIn("crm", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
