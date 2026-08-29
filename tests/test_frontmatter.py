"""The file format every connector writes, and the two properties that hold the
whole design together.

  - **A stable slug.** Re-running a connector must overwrite the same file, or
    the graph accumulates a duplicate node per sync — which looks like the
    source growing, not like a bug.
  - **Escaping that survives hostile input.** Field values come from other
    people's systems: a CRM record whose title is `" \\n owner: admin` must not
    be able to close the quoted scalar and inject a frontmatter key.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brainiphy_cli.frontmatter import slugify, write_record, yaml_str  # noqa: E402
from support import TempProjectTestCase  # noqa: E402


class SlugifyTests(unittest.TestCase):
    def test_the_same_id_always_gives_the_same_slug(self):
        self.assertEqual(slugify("abc-123"), slugify("abc-123"))

    def test_ids_that_differ_give_slugs_that_differ(self):
        self.assertNotEqual(slugify("contact-1"), slugify("contact-2"))

    def test_path_separators_cannot_escape_the_output_directory(self):
        # A remote ID is attacker-influenced in exactly the way a filename must
        # not be: this is the difference between writing into raw/<name>/ and
        # writing into the user's home directory.
        for hostile in ("../../etc/passwd", "/etc/passwd", "a/b/c", "..", "."):
            with self.subTest(hostile=hostile):
                slug = slugify(hostile)
                self.assertNotIn("/", slug)
                self.assertNotIn("..", slug)

    def test_the_two_unicode_spellings_of_a_name_give_one_slug(self):
        # macOS hands out decomposed filenames and most APIs send composed
        # ones, so the same record can arrive spelled two ways. NFKD folding
        # is what stops that from becoming two nodes in the graph.
        composed = "caf\u00e9"        # e-acute as one code point
        decomposed = "cafe\u0301"     # e + combining acute
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(slugify(composed), slugify(decomposed))

    def test_long_ids_are_truncated_but_still_stable(self):
        long_id = "x" * 500
        self.assertLessEqual(len(slugify(long_id)), 80)
        self.assertEqual(slugify(long_id), slugify(long_id))

    def test_an_id_with_nothing_usable_still_yields_a_filename(self):
        self.assertEqual(slugify("///"), "item")
        self.assertEqual(slugify(""), "item")


class YamlStrTests(unittest.TestCase):
    def test_quotes_and_backslashes_are_escaped(self):
        self.assertEqual(yaml_str('a "b" c'), 'a \\"b\\" c')
        self.assertEqual(yaml_str("a\\b"), "a\\\\b")

    def test_newlines_do_not_survive_as_line_breaks(self):
        self.assertNotIn("\n", yaml_str("line one\nline two"))

    def test_control_characters_become_escapes(self):
        self.assertEqual(yaml_str("\x07"), "\\x07")
        # U+2028/U+2029 are line breaks to a YAML parser but invisible in an
        # editor, which is exactly why they get their own escape.
        self.assertEqual(yaml_str("\u2028"), "\\L")
        self.assertEqual(yaml_str("\u2029"), "\\P")

    def test_none_renders_as_an_empty_value(self):
        self.assertEqual(yaml_str(None), "")

    def test_a_hostile_title_cannot_inject_a_key(self):
        """The property, checked through a real YAML parser rather than by
        eyeballing the escaping."""
        hostile = 'pwned"\ninjected: yes\nx: "'
        document = f'title: "{yaml_str(hostile)}"\n'
        parsed = yaml.safe_load(document)
        self.assertEqual(parsed, {"title": hostile})
        self.assertNotIn("injected", parsed)


class WriteRecordTests(TempProjectTestCase):
    def _write(self, **kwargs):
        defaults = dict(
            record_id="rec-1",
            title="A record",
            body="Some body text.",
            source_system="crm",
        )
        defaults.update(kwargs)
        return write_record(self.tmp / "out", **defaults)

    def test_writing_the_same_record_twice_leaves_one_file(self):
        # Idempotent sync: same slug in, same file out. The graph gets one node.
        first = self._write()
        second = self._write(title="A record, renamed")
        self.assertEqual(first, second)
        self.assertEqual(len(list((self.tmp / "out").glob("*.md"))), 1)

    def test_the_frontmatter_parses_as_yaml(self):
        path = self._write(extra_fields={"stage": "Awaiting payment"})
        _, frontmatter, body = path.read_text(encoding="utf-8").split("---", 2)
        data = yaml.safe_load(frontmatter)
        self.assertEqual(data["source_id"], "rec-1")
        self.assertEqual(data["source_system"], "crm")
        self.assertEqual(data["stage"], "Awaiting payment")
        self.assertIn("Some body text.", body)

    def test_a_hostile_record_still_produces_valid_yaml(self):
        # The end-to-end version of the escaping property: a CRM title trying to
        # break out of the scalar reaches disk as data, not as structure.
        path = self._write(title='Deal "X"\nowner: attacker', record_id='id"1')
        _, frontmatter, _ = path.read_text(encoding="utf-8").split("---", 2)
        data = yaml.safe_load(frontmatter)
        self.assertNotIn("owner", data)
        self.assertEqual(data["source_id"], 'id"1')

    def test_the_output_directory_is_created_on_demand(self):
        # A connector should not have to mkdir before it can write its first
        # record; --out may point anywhere.
        path = write_record(self.tmp / "deep" / "nested" / "out", record_id="a",
                            title="t", body="b", source_system="s")
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
