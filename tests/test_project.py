"""Creating a connector, and the small pieces the rest of the tool trusts.

create_connector picks one of four templates and fills constants into it. What
makes it worth testing is that every failure here is quiet: a constant that did
not get substituted leaves a connector that runs and reads the wrong account, a
type recorded wrong shows the user a mislabeled source forever, and a template
overwritten in place destroys work that cannot be recovered.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brainiphy_cli import project as project_mod, steps  # noqa: E402
from support import TempProjectTestCase, quiet  # noqa: E402


class SetConstantTests(unittest.TestCase):
    def test_a_whole_assignment_line_is_rewritten(self):
        text, ok = project_mod.set_constant('X = "old"\nY = 1\n', "X", '"new"')
        self.assertTrue(ok)
        self.assertIn('X = "new"', text)
        self.assertIn("Y = 1", text)

    def test_a_windows_style_path_survives_substitution(self):
        # The documented reason set_constant uses a lambda: re.sub would read
        # \n and \t in a path as escape sequences and write a literal newline
        # into the generated connector.
        value = repr("C:\\Users\\new\\test")
        text, ok = project_mod.set_constant("MIRROR_SOURCE = None\n", "MIRROR_SOURCE", value)
        self.assertTrue(ok)
        self.assertIn("\\\\Users", text)
        self.assertNotIn("\n\new", text)

    def test_a_constant_that_is_not_there_reports_false(self):
        # This is what makes `--var TYPO=x` a warning rather than a silent no-op.
        _, ok = project_mod.set_constant("X = 1\n", "NOPE", "2")
        self.assertFalse(ok)

    def test_only_the_first_occurrence_is_rewritten(self):
        text, _ = project_mod.set_constant('A = "1"\nA = "2"\n', "A", '"3"')
        self.assertEqual(text, 'A = "3"\nA = "2"\n')


PLACEHOLDER_SOURCE = (
    'LOCATION_ID = "REPLACE_ME"\n'
    'API_VERSION = "REPLACE_ME_2"\n'
    'TOKEN_KIND = "REPLACE_ME_TOKEN"\n'
    'FINE = "an actual value"\n'
)


class PlaceholderAgreementTests(TempProjectTestCase):
    """The installer's warning and the checklist's blocker must see the same
    constants.

    They used to be two regexes, and they disagreed about a digit in the
    suffix: `brain new-connector` reported a connector as ready to run while
    `brain status` refused to tick step 4 for it, with no way to tell which was
    right from either screen.
    """

    def test_every_unfilled_constant_is_found(self):
        found = project_mod.unfilled_placeholders(PLACEHOLDER_SOURCE)
        self.assertEqual(found, ["LOCATION_ID", "API_VERSION", "TOKEN_KIND"])
        self.assertNotIn("FINE", found)

    def test_the_checklist_reports_exactly_those_constants(self):
        self.scaffold()
        self.write_connector("crm", PLACEHOLDER_SOURCE)
        detail = next(s for s in steps.inspect(self.project).steps
                      if s.key == "implement").detail
        for constant in project_mod.unfilled_placeholders(PLACEHOLDER_SOURCE):
            self.assertIn(constant, detail)
        self.assertEqual(steps.unimplemented_connectors(self.project), ["crm"])


class CreateConnectorTests(TempProjectTestCase):
    def setUp(self):
        super().setUp()
        self.scaffold()

    def _create(self, name, **kwargs):
        with quiet() as buf:
            path = project_mod.create_connector(self.project, name, **kwargs)
        return path, buf.getvalue()

    def _entry(self, name):
        return next(e for e in project_mod.load_registry_entries(self.project)
                    if e["name"] == name)

    def test_a_mirror_connector_is_complete_and_typed(self):
        source = self.tmp / "source-docs"
        source.mkdir()
        path, _ = self._create("docs", mirror=source)
        text = path.read_text()
        self.assertIn(repr(str(source)), text)
        self.assertNotIn("REPLACE_ME", text)
        self.assertNotIn("raise NotImplementedError", text)
        self.assertEqual(self._entry("docs")["type"], project_mod.LOCAL_FOLDER)
        self.assertEqual(steps.unimplemented_connectors(self.project), [])

    def test_an_api_connector_gets_its_base_url_and_secret_item(self):
        path, _ = self._create("billing", api_base="https://api.example.com/")
        text = path.read_text()
        # The trailing slash is stripped, or every request URL gets a double one.
        self.assertIn("BASE_URL = 'https://api.example.com'", text)
        self.assertIn(project_mod.secret_item_name(self.project, "billing"), text)
        self.assertEqual(self._entry("billing")["type"], project_mod.HTTP_API)

    def test_an_api_connector_is_still_unfinished(self):
        # The plumbing is written, the endpoints are not — step 4 must say so.
        self._create("billing", api_base="https://api.example.com")
        self.assertEqual(steps.unimplemented_connectors(self.project), ["billing"])

    def test_a_preset_keeps_the_vendor_as_its_source_system(self):
        # Records must carry the same provenance across projects: a GHL contact
        # is source_system gohighlevel, not "crm" because that is what this
        # brain happened to call the connector.
        path, _ = self._create("crm", preset="gohighlevel")
        self.assertIsNotNone(path)
        text = path.read_text()
        self.assertIn("gohighlevel", text)
        self.assertNotIn('SOURCE_SYSTEM = \'crm\'', text)
        # A preset records its own name, which identifies the source far better
        # than the generic "http-api" would.
        self.assertEqual(self._entry("crm")["type"], "gohighlevel")

    def test_a_var_can_override_a_computed_default(self):
        # --var is applied last on purpose: a connector may legitimately point
        # at a Keychain item that is not the conventional one.
        path, _ = self._create("crm", preset="gohighlevel",
                               variables={"SECRET_ITEM": "shared-ghl-token"})
        self.assertIn("SECRET_ITEM = 'shared-ghl-token'", path.read_text())

    def test_a_var_that_is_not_a_constant_warns_instead_of_passing_silently(self):
        _, printed = self._create("crm", preset="gohighlevel",
                                  variables={"NOT_A_CONSTANT": "x"})
        self.assertIn("NOT_A_CONSTANT", printed)

    def test_the_bare_template_is_the_fallback(self):
        path, _ = self._create("weird-db")
        self.assertIn("fetch_records", path.read_text())
        self.assertEqual(self._entry("weird-db")["type"], project_mod.CUSTOM)

    def test_an_existing_script_is_never_overwritten(self):
        # It may hold hours of hand-written fetching. Refusing is the only safe
        # answer; the user can delete it themselves.
        path, _ = self._create("crm")
        path.write_text("# my real work\n", encoding="utf-8")
        again, printed = self._create("crm")
        self.assertIsNone(again)
        self.assertEqual(path.read_text(), "# my real work\n")

    def test_an_unknown_preset_is_refused_and_lists_the_real_ones(self):
        path, printed = self._create("crm", preset="salesforce")
        self.assertIsNone(path)
        self.assertIn("gohighlevel", printed)

    def test_the_generated_script_is_executable(self):
        path, _ = self._create("crm")
        self.assertTrue(path.stat().st_mode & 0o111)

    def test_registering_the_same_name_twice_does_not_duplicate_it(self):
        source = self.tmp / "src"
        source.mkdir()
        self._create("docs", mirror=source)
        (self.project / "connectors" / "docs" / "sync.py").unlink()
        self._create("docs", mirror=source)
        names = [e["name"] for e in project_mod.load_registry_entries(self.project)]
        self.assertEqual(names.count("docs"), 1)


class TypeLabelTests(TempProjectTestCase):
    def test_a_recorded_type_is_shown_readably(self):
        self.assertEqual(project_mod.type_label({"type": "local-folder"}), "local folder")
        self.assertEqual(project_mod.type_label({"type": "http-api"}), "http api")

    def test_a_preset_name_is_shown_as_itself(self):
        self.assertEqual(project_mod.type_label({"type": "gohighlevel"}), "gohighlevel")

    def test_an_old_entry_is_inferred_from_its_script(self):
        # Brains created before `type:` existed must not render a column of
        # dashes; the generated script says what it is.
        self.scaffold()
        self.write_connector("docs", "MIRROR_SOURCE = Path('/x')\n", register=False)
        self.write_connector("api", "BASE_URL = 'https://x'\n", register=False)
        self.assertEqual(project_mod.type_label({"name": "docs"}, self.project), "local folder")
        self.assertEqual(project_mod.type_label({"name": "api"}, self.project), "http api")

    def test_an_entry_with_nothing_to_go_on_renders_a_dash(self):
        self.assertEqual(project_mod.type_label({"name": "gone"}, self.project), "—")


class AgentPathTests(unittest.TestCase):
    """The PATH baked into the LaunchAgent."""

    def test_the_launchd_default_is_always_the_tail(self):
        # Everything a scheduled job could otherwise find still has to be
        # findable; the resolved dirs are added in front, never instead.
        path = project_mod._agent_path()
        self.assertTrue(path.endswith(project_mod._LAUNCHD_DEFAULT_PATH))

    def test_the_tools_a_sync_needs_are_on_it(self):
        fake = {"brain": "/opt/tools/brain",
                "graphify": "/opt/tools/graphify",
                "claude": "/home/u/.local/bin/claude"}
        with mock.patch.object(project_mod.shutil, "which", side_effect=fake.get):
            entries = project_mod._agent_path().split(":")
        self.assertIn("/opt/tools", entries)
        self.assertIn("/home/u/.local/bin", entries)

    def test_a_shared_directory_is_listed_once(self):
        with mock.patch.object(project_mod.shutil, "which", return_value="/usr/local/bin/x"):
            entries = project_mod._agent_path().split(":")
        self.assertEqual(entries.count("/usr/local/bin"), 1)

    def test_a_missing_tool_is_simply_left_out(self):
        # claude not installed is a normal state, not a reason to fail to
        # schedule — the sync just has no subscription backend available.
        with mock.patch.object(project_mod.shutil, "which", return_value=None):
            self.assertEqual(project_mod._agent_path(), project_mod._LAUNCHD_DEFAULT_PATH)

    def test_the_symlink_directory_is_kept_rather_than_resolved(self):
        """`claude` is a symlink into a versioned install dir that contains no
        binary called `claude`. Resolving it would pin a PATH entry that is
        useless now and stale after the next update."""
        with mock.patch.object(project_mod.shutil, "which",
                               side_effect={"claude": "/home/u/.local/bin/claude"}.get):
            entries = project_mod._agent_path().split(":")
        self.assertIn("/home/u/.local/bin", entries)


class ScaffoldTests(TempProjectTestCase):
    def test_scaffolding_writes_what_graphify_needs(self):
        self.scaffold()
        graphifyignore = (self.project / ".graphifyignore").read_text()
        gitignore = (self.project / ".gitignore").read_text()
        # Without this, graphify's AST extractor indexes the connector scripts
        # themselves as if they were the business's source code.
        self.assertIn("connectors/", graphifyignore)
        # And this is why every graphify call passes --no-gitignore.
        self.assertIn("raw/", gitignore)

    def test_scaffolding_twice_changes_nothing(self):
        self.scaffold()
        before = (self.project / ".gitignore").read_text()
        self.write_connector("crm", "# real work\n")
        self.scaffold()
        self.assertEqual((self.project / ".gitignore").read_text(), before)
        # And it must not have reset a registry that already has connectors in it.
        self.assertEqual(len(project_mod.load_registry_entries(self.project)), 1)

    def test_ignore_entries_are_appended_not_replaced(self):
        self.project.mkdir(parents=True)
        (self.project / ".gitignore").write_text("# mine\n*.log\n", encoding="utf-8")
        self.scaffold()
        text = (self.project / ".gitignore").read_text()
        self.assertIn("*.log", text)
        self.assertIn("raw/", text)


if __name__ == "__main__":
    unittest.main()
