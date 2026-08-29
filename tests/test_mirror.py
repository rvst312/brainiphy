"""Turning the parts of a folder graphify cannot read into records.

graphify indexes .md/.txt/.rst/.html/.yaml, .pdf, images and .docx/.xlsx, and
silently ignores the rest. A real client folder is full of the rest — a CSV
export, a JSON dump — and those were being copied into the brain and skipped
without a word: the folder looked full while the graph was nearly empty.

These tests cover the conversion itself. The template is copied as text into a
generated connector and never imported by the package, but it is a normal
module, so the pure parts are testable here and the end-to-end shape is left to
scripts/smoke.sh.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brainiphy_cli import mirror_template as mirror  # noqa: E402
from support import TempProjectTestCase  # noqa: E402


class FieldNameTests(unittest.TestCase):
    def test_a_header_becomes_a_usable_yaml_key(self):
        # Real headers are like this, and left alone they produce frontmatter
        # nobody can query — or a key a parser reads as something else.
        self.assertEqual(mirror.field_name("Importe (€)", 0), "importe")
        self.assertEqual(mirror.field_name("Fecha de alta", 1), "fecha_de_alta")
        self.assertEqual(mirror.field_name("  NAME  ", 2), "name")

    def test_a_header_with_nothing_usable_falls_back_to_its_position(self):
        self.assertEqual(mirror.field_name("", 3), "column_4")
        self.assertEqual(mirror.field_name("€€€", 0), "column_1")


class TableTests(TempProjectTestCase):
    def _csv(self, text: str, name: str = "facturacion.csv") -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_one_record_per_row(self):
        # The reason for per-row rather than one document holding a table: the
        # graph gets entities it can relate, not a single node with a table in it.
        records, capped = mirror.records_from_table(
            self._csv("Cliente,Importe\nacme,1200\nmoda lunar,890\n"))
        self.assertEqual(len(records), 2)
        self.assertFalse(capped)
        self.assertEqual(records[0]["fields"], {"cliente": "acme", "importe": "1200"})

    def test_the_title_is_the_name_in_the_row_not_its_number(self):
        # "acme" beats "row 12" by a distance when it is the label on a node.
        records, _ = mirror.records_from_table(
            self._csv("Cliente,Importe\nacme,1200\n"))
        self.assertEqual(records[0]["title"], "acme")

    def test_a_row_with_no_obvious_name_still_gets_a_title(self):
        records, _ = mirror.records_from_table(self._csv("a,b\n,42\n"))
        self.assertEqual(records[0]["title"], "42")

    def test_the_id_ties_a_record_back_to_its_row(self):
        # Stable across runs, so a re-sync overwrites instead of duplicating.
        records, _ = mirror.records_from_table(
            self._csv("Cliente\nacme\nmoda\n"))
        self.assertEqual([r["id"] for r in records],
                         ["facturacion.csv:1", "facturacion.csv:2"])

    def test_blank_rows_are_left_out(self):
        records, _ = mirror.records_from_table(
            self._csv("Cliente,Importe\nacme,1200\n,\n\nmoda,890\n"))
        self.assertEqual(len(records), 2)

    def test_an_empty_file_produces_nothing_rather_than_raising(self):
        self.assertEqual(mirror.records_from_table(self._csv("")), ([], False))

    def test_a_header_only_file_produces_nothing(self):
        records, _ = mirror.records_from_table(self._csv("Cliente,Importe\n"))
        self.assertEqual(records, [])

    def test_tabs_are_read_as_a_tsv(self):
        records, _ = mirror.records_from_table(
            self._csv("Cliente\tImporte\nacme\t1200\n", name="x.tsv"))
        self.assertEqual(records[0]["fields"], {"cliente": "acme", "importe": "1200"})

    def test_a_huge_export_is_capped_and_says_so(self):
        # A 200k-row export would otherwise bury every other source in the brain.
        rows = "\n".join(f"cliente{i},{i}" for i in range(mirror.MAX_ROWS_PER_TABLE + 50))
        records, capped = mirror.records_from_table(self._csv(f"Cliente,Importe\n{rows}\n"))
        self.assertEqual(len(records), mirror.MAX_ROWS_PER_TABLE)
        self.assertTrue(capped)

    def test_a_row_longer_than_its_header_does_not_raise(self):
        # Ragged CSVs exist; losing the file is worse than losing the extra cell.
        records, _ = mirror.records_from_table(self._csv("a,b\n1,2,3\n"))
        self.assertEqual(records[0]["fields"], {"a": "1", "b": "2"})


class JsonTests(TempProjectTestCase):
    def _json(self, text: str) -> Path:
        path = self.tmp / "datos.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_list_of_objects_becomes_one_record_each(self):
        records, _ = mirror.records_from_json(
            self._json('[{"nombre":"Talleres Vidal"},{"nombre":"Bar Pepe"}]'))
        self.assertEqual([r["title"] for r in records], ["Talleres Vidal", "Bar Pepe"])

    def test_a_single_object_becomes_one_record(self):
        records, _ = mirror.records_from_json(self._json('{"nombre":"acme"}'))
        self.assertEqual(len(records), 1)

    def test_a_list_of_scalars_has_no_records_to_make(self):
        records, _ = mirror.records_from_json(self._json('[1, 2, 3]'))
        self.assertEqual(records, [])

    def test_malformed_json_is_skipped_rather_than_fatal(self):
        # One bad file must not cost the whole sync.
        self.assertEqual(mirror.records_from_json(self._json("{ nope")), ([], False))

    def test_a_long_list_is_capped(self):
        items = ",".join(f'{{"n":{i}}}' for i in range(mirror.MAX_ROWS_PER_TABLE + 10))
        records, capped = mirror.records_from_json(self._json(f"[{items}]"))
        self.assertEqual(len(records), mirror.MAX_ROWS_PER_TABLE)
        self.assertTrue(capped)


class ConvertTests(TempProjectTestCase):
    def setUp(self):
        super().setUp()
        self.out = self.tmp / "derived"
        self.source = self.tmp / "facturacion.csv"
        self.source.write_text(
            "Cliente,Importe (€)\nacme,1200\nmoda lunar,890\n", encoding="utf-8")

    def test_records_land_as_markdown_with_queryable_frontmatter(self):
        written, capped = mirror.convert(self.source, self.out, Path("facturacion.csv"))
        self.assertEqual(written, 2)
        self.assertFalse(capped)

        files = sorted((self.out / "facturacion").glob("*.md"))
        self.assertEqual(len(files), 2)
        _, frontmatter, body = files[0].read_text().split("---", 2)
        data = yaml.safe_load(frontmatter)
        # The point of frontmatter over prose: these are filterable.
        self.assertEqual(data["cliente"], "acme")
        self.assertEqual(data["importe"], "1200")
        # And every record says which file it came from.
        self.assertEqual(data["source_file"], "facturacion.csv")
        self.assertIn("acme", body)

    def test_converting_twice_overwrites_rather_than_duplicating(self):
        mirror.convert(self.source, self.out, Path("facturacion.csv"))
        mirror.convert(self.source, self.out, Path("facturacion.csv"))
        self.assertEqual(len(list((self.out / "facturacion").glob("*.md"))), 2)

    def test_a_file_type_it_cannot_read_is_left_alone(self):
        other = self.tmp / "hoja.numbers"
        other.write_text("binary-ish", encoding="utf-8")
        self.assertEqual(mirror.convert(other, self.out, Path("hoja.numbers")), (0, False))
        self.assertFalse(self.out.exists())

    def test_nested_files_keep_their_folder(self):
        # Two exports called facturacion.csv in different folders must not
        # collide into one set of records.
        nested = self.tmp / "clientes"
        nested.mkdir()
        source = nested / "facturacion.csv"
        source.write_text("Cliente\nacme\n", encoding="utf-8")
        mirror.convert(source, self.out, Path("clientes/facturacion.csv"))
        self.assertTrue((self.out / "clientes" / "facturacion").is_dir())


class NativeExtensionTests(unittest.TestCase):
    def test_what_graphify_reads_is_never_converted(self):
        """Converting a document graphify already understands would only put a
        worse copy of it in the graph."""
        for suffix in (".md", ".txt", ".pdf", ".docx", ".xlsx", ".html", ".png"):
            with self.subTest(suffix=suffix):
                self.assertIn(suffix, mirror.NATIVE_EXTENSIONS)
                self.assertNotIn(suffix, mirror.TABLE_EXTENSIONS)
                self.assertNotIn(suffix, mirror.JSON_EXTENSIONS)

    def test_the_derived_folder_is_kept_out_of_the_mirror(self):
        # rsync --delete removes anything in the destination with no
        # counterpart in the source, which is exactly what the converted
        # records are. Without the exclude they are written and deleted on
        # every single run.
        self.assertIn("EXCLUDES", dir(mirror))
        template = Path(mirror.__file__).read_text()
        self.assertIn('"--exclude", f"{DERIVED_DIRNAME}/"', template)


if __name__ == "__main__":
    unittest.main()
