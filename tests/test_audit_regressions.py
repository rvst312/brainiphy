"""Boundary and recovery regressions from the code audit.

All projects and configuration are temporary. HTTP, Keychain and launchd are
replaced; no real credential or service is touched.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brainiphy_cli import collect, frontmatter, httpclient, keychain, project, steps, sync
from support import TempProjectTestCase, quiet


def record(rid, **extra):
    return {"id": rid, "title": str(rid), "body": "content", **extra}


class HTTPBoundaryTests(TempProjectTestCase):
    def test_external_page_is_refused_before_credentials_are_read(self):
        client = httpclient.HttpClient("https://api.example.com", secret_item="test")
        for url in ("https://other.example.com/x", "http://api.example.com/x",
                    "https://api.example.com:444/x", "https://user@api.example.com/x"):
            with self.subTest(url=url), mock.patch.object(client._opener, "open") as opened, \
                    mock.patch.object(httpclient, "get_secret") as secret:
                with self.assertRaises(ValueError):
                    client.request(url)
                opened.assert_not_called()
                secret.assert_not_called()

    def test_paginated_external_url_never_receives_an_authorization_header(self):
        client = httpclient.HttpClient("https://api.example.com", token="synthetic")
        payload = {"items": [record("1")], "meta": {"nextPageUrl": "https://other.example/x"}}
        response = contextlib.closing(io.BytesIO(json.dumps(payload).encode()))
        with mock.patch.object(client._opener, "open", return_value=response) as opened:
            with self.assertRaises(ValueError):
                client.paginate("/x", {}, "items")
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(opened.call_args.args[0].host, "api.example.com")

    def test_redirect_checks_scheme_host_and_port(self):
        handler = httpclient._SameOriginRedirect()
        req = urllib.request.Request("https://api.example.com/start", headers={"X-Api-Key": "synthetic"})
        for target in ("https://other.example/", "http://api.example.com/", "https://api.example.com:444/"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                handler.redirect_request(req, None, 302, "Found", {}, target)
        redirected = handler.redirect_request(req, None, 302, "Found", {}, "https://api.example.com:443/end")
        self.assertEqual(redirected.get_header("X-api-key"), "synthetic")


class SnapshotTests(TempProjectTestCase):
    def setUp(self):
        super().setUp()
        self.out = self.tmp / "out"

    def run_collectors(self, collectors, *flags):
        for collector in collectors:
            collector.snapshot = True
        with quiet():
            return collect.run("crm", collectors, argv=["--out", str(self.out), *flags])

    def snapshot(self, records):
        return self.run_collectors([collect.Collector("contacts", "contacts", lambda: records)])

    def files(self, folder="contacts"):
        return {p.name: p.read_bytes() for p in (self.out / folder).glob("*.md")}

    def test_deleted_record_is_removed_and_empty_snapshot_clears_owned_files(self):
        self.snapshot([record("1"), record("2")])
        self.snapshot([record("1")])
        self.assertEqual(set(self.files()), {frontmatter.record_filename("1")})
        self.snapshot([])
        self.assertEqual(self.files(), {})

    def test_missing_scope_or_incomplete_fetch_preserves_the_snapshot(self):
        self.snapshot([record("1"), record("2")])
        before = self.files()
        for error, exit_code in [(httpclient.NoScope("denied"), 0),
                                 (httpclient.IncompleteFetch("cap"), 1), (ValueError("bad data"), 1)]:
            with self.subTest(error=type(error).__name__):
                def broken():
                    raise error
                self.assertEqual(self.run_collectors([collect.Collector("contacts", "contacts", broken)]), exit_code)
                self.assertEqual(self.files(), before)

    def test_invalid_record_preserves_snapshot_and_does_not_stop_next_collector(self):
        self.snapshot([record("old")])
        before = self.files()
        code = self.run_collectors([
            collect.Collector("contacts", "contacts", lambda: [record("new"), {"id": "bad"}]),
            collect.Collector("deals", "deals", lambda: [record("deal")]),
        ])
        self.assertEqual(code, 1)
        self.assertEqual(self.files(), before)
        self.assertEqual(len(self.files("deals")), 1)

    def test_unchanged_snapshot_keeps_file_contents_and_mtime(self):
        self.snapshot([record("1")])
        path = self.out / "contacts" / frontmatter.record_filename("1")
        before, stamp = path.read_bytes(), path.stat().st_mtime_ns
        self.snapshot([record("1")])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_shared_folder_collectors_do_not_prune_each_other(self):
        collectors = [collect.Collector("tags", "schema", lambda: [record("tags")]),
                      collect.Collector("fields", "schema", lambda: [record("fields")])]
        self.run_collectors(collectors)
        collectors[0].fetch = lambda: []
        self.run_collectors(collectors, "--only", "tags")
        self.assertEqual(set(self.files("schema")), {frontmatter.record_filename("fields")})

    def test_legacy_record_is_migrated_without_duplicate_and_unowned_file_survives(self):
        target = self.out / "contacts"
        target.mkdir(parents=True)
        legacy = target / "old.md"
        legacy.write_text('---\nsource_id: old\nsource_system: crm\n---\nold\n')
        unowned = target / "manual.md"
        unowned.write_text("# My manual note\n")
        self.snapshot([record("old")])
        self.assertFalse(legacy.exists())
        self.assertTrue((target / frontmatter.record_filename("old")).exists())
        self.assertTrue(unowned.exists())

    def test_duplicate_ids_refuse_publish(self):
        self.snapshot([record("old")])
        before = self.files()
        self.assertEqual(self.snapshot([record("1"), record("1")]), 1)
        self.assertEqual(self.files(), before)

    def test_probe_never_prunes_records(self):
        self.snapshot([record("1")])
        before = self.files()
        self.run_collectors([collect.Collector("contacts", "contacts", lambda: [])], "--probe")
        self.assertEqual(self.files(), before)

    def test_collector_output_symlink_cannot_escape(self):
        external = self.tmp / "external"
        external.mkdir()
        self.out.mkdir()
        (self.out / "contacts").symlink_to(external, target_is_directory=True)
        self.assertEqual(self.snapshot([record("1")]), 1)
        self.assertEqual(list(external.iterdir()), [])


class RecordIdentityTests(TempProjectTestCase):
    def test_lossy_slug_pairs_remain_distinct_on_disk(self):
        ids = ["a/b", "a?b", "x" * 100 + "a", "x" * 100 + "b", "中文", "日本語", "CAFÉ", "CAFE", "café", "cafe\u0301"]
        paths = [frontmatter.write_record(self.tmp / "out", record_id=i, title=i, body="body", source_system="crm") for i in ids]
        self.assertEqual(len(set(paths)), len(ids))
        self.assertEqual(len(list((self.tmp / "out").glob("*.md"))), len(ids))
        for path, rid in zip(paths, ids):
            self.assertEqual(frontmatter.read_metadata(path)["source_id"], rid)

    def test_untrusted_keys_cannot_replace_provenance_or_inject_yaml(self):
        path = frontmatter.write_record(self.tmp, record_id="1", title="title", body="body", source_system="crm",
                                       extra_fields={"source_system": "attacker", "field\ninjected": "data"})
        metadata = frontmatter.read_metadata(path)
        self.assertEqual(metadata["source_system"], "crm")
        self.assertEqual(metadata["field\ninjected"], "data")
        self.assertNotIn("injected", metadata)


class ProjectBoundaryTests(TempProjectTestCase):
    def test_invalid_connector_names_write_nothing(self):
        self.scaffold()
        before = set(self.tmp.rglob("*"))
        for name in ("../escape", "../../escape", "/tmp/escape", ".", "..", "", "a/b", "a\\b", 123):
            with self.subTest(name=name), quiet():
                self.assertIsNone(project.create_connector(self.project, name))
        self.assertEqual(set(self.tmp.rglob("*")), before)

    def test_connector_directory_symlink_cannot_escape(self):
        self.scaffold()
        external = self.tmp / "external"
        external.mkdir()
        (self.project / "connectors" / "escape").symlink_to(external, target_is_directory=True)
        with quiet():
            self.assertIsNone(project.create_connector(self.project, "escape"))
        self.assertEqual(list(external.iterdir()), [])

    def test_untrusted_registry_names_are_not_executed(self):
        self.scaffold()
        project.write_registry_entries(self.project, [{"name": "../../escape"}, {"name": 12}])
        with mock.patch.object(sync.subprocess, "run") as runner, quiet():
            report = sync.run(self.project)
        self.assertEqual(len(report.errors), 2)
        runner.assert_not_called()
        self.assertEqual(steps.unimplemented_connectors(self.project), [])

    def test_mirror_rejects_overlapping_source_and_output(self):
        self.scaffold()
        with quiet():
            self.assertIsNone(project.create_connector(self.project, "docs", mirror=self.project))

    def test_commented_ignore_pattern_is_not_treated_as_active(self):
        self.project.mkdir()
        (self.project / ".graphifyignore").write_text("# connectors/\nother/connectors/\n")
        self.scaffold()
        self.assertIn("connectors/", (self.project / ".graphifyignore").read_text().splitlines())


class NamespaceTests(TempProjectTestCase):
    def test_same_folder_names_have_distinct_stable_namespaces(self):
        first, second = self.tmp / "client-a" / "brain", self.tmp / "client-b" / "brain"
        a = project.project_namespace(first, create=True)
        b = project.project_namespace(second, create=True)
        self.assertNotEqual(a, b)
        self.assertNotEqual(project.secret_item_name(first, "crm"), project.secret_item_name(second, "crm"))
        moved = self.tmp / "renamed"
        first.rename(moved)
        self.assertEqual(project.project_namespace(moved), a)
        self.assertNotEqual(project.project_namespace(first, create=True), a)

    def test_namespace_inspection_is_read_only(self):
        project.project_namespace(self.project)
        self.assertFalse(self.project.exists())

    def test_migration_copies_legacy_secret_without_deleting_it_and_preserves_custom_reference(self):
        self.scaffold()
        legacy = f"graphify-{project.slug(self.project.name)}-crm"
        script = self.write_connector("crm", f"SECRET_ITEM = {legacy!r}\n# real work\n")
        script.chmod(0o755)
        custom = self.write_connector("custom", "SECRET_ITEM = 'explicit-shared-key'\n")
        store = {legacy: "synthetic-value"}
        def get(item):
            if item not in store:
                raise keychain.SecretNotFoundError(item)
            return store[item]
        with mock.patch.object(keychain, "get_secret", side_effect=get), \
                mock.patch.object(keychain, "set_secret", side_effect=lambda k, v: store.update({k: v})), quiet():
            self.assertTrue(project.migrate_identity(self.project))
            self.assertTrue(project.migrate_identity(self.project))
        new = project.secret_item_name(self.project, "crm")
        self.assertEqual(store[new], store[legacy])
        self.assertIn(new, script.read_text())
        self.assertIn("# real work", script.read_text())
        self.assertEqual(script.stat().st_mode & 0o777, 0o755)
        self.assertEqual(custom.read_text(), "SECRET_ITEM = 'explicit-shared-key'\n")

    def test_failed_secret_copy_does_not_change_connector(self):
        self.scaffold()
        legacy = f"graphify-{project.slug(self.project.name)}-crm"
        script = self.write_connector("crm", f"SECRET_ITEM = {legacy!r}\n")
        before = script.read_bytes()
        with mock.patch.object(keychain, "get_secret", side_effect=keychain.SecretNotFoundError("missing")), quiet():
            self.assertFalse(project.migrate_identity(self.project))
        self.assertEqual(script.read_bytes(), before)

    def test_secret_reference_parses_escaped_literals_and_custom_overrides(self):
        self.scaffold()
        self.write_connector("crm", 'SECRET_ITEM = "a\\\\b"\n')
        self.assertEqual(project.connector_secret_item(self.project, "crm"), "a\\b")

    def test_desktop_same_named_projects_keep_both_servers(self):
        first, second = self.tmp / "a" / "brain", self.tmp / "b" / "brain"
        config = self.tmp / "desktop.json"
        config.write_text('{"mcpServers":{"unrelated":{"command":"keep"}}}')
        with mock.patch.object(project, "DESKTOP_CONFIG", config), \
                mock.patch.object(project, "find_exe", side_effect=lambda n: "/bin/" + n), \
                mock.patch.object(project.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), quiet():
            for brain in (first, second):
                brain.mkdir(parents=True)
                self.assertTrue(project.connect_claude(brain, desktop=True, trust_desktop=True))
        data = json.loads(config.read_text())
        self.assertEqual(len(data["mcpServers"]), 3)
        self.assertEqual(len(data["preferences"]["localAgentModeTrustedFolders"]), 2)


class ScheduleTests(TempProjectTestCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        patcher = mock.patch.object(Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(project, "find_exe", return_value="/bin/brain & test")
        patcher.start()
        self.addCleanup(patcher.stop)

    def schedule(self, brain, **kwargs):
        project.write_registry_entries(brain, [{"name": "demo"}])
        with quiet():
            return project.schedule(brain, **kwargs)

    def test_xml_characters_in_paths_round_trip(self):
        brain = self.tmp / "A & B <work>"
        self.assertTrue(self.schedule(brain))
        path = project.launch_agent(brain)
        data = plistlib.loads(path.read_bytes())
        self.assertEqual(data["ProgramArguments"], ["/bin/brain & test", "sync", str(brain), "--full"])
        self.assertEqual(data["StartInterval"], 900)

    def test_same_names_and_custom_slugs_are_detected_by_project_path(self):
        first, second = self.tmp / "a" / "brain", self.tmp / "b" / "brain"
        self.assertTrue(self.schedule(first, slug_override="special"))
        self.assertIsNone(steps._launch_agent(second))
        self.assertTrue(self.schedule(second))
        self.assertNotEqual(steps._launch_agent(first), steps._launch_agent(second))

    def test_custom_slug_cannot_replace_another_project_schedule(self):
        first, second = self.tmp / "a", self.tmp / "b"
        self.assertTrue(self.schedule(first, slug_override="shared"))
        path = project.launch_agent(first)
        before = path.read_bytes()
        self.assertFalse(self.schedule(second, slug_override="shared"))
        self.assertEqual(path.read_bytes(), before)


class BuildRecoveryTests(TempProjectTestCase):
    def test_failed_build_is_retried_without_repolling_connectors(self):
        self.scaffold()
        self.write_connector("demo", "print('ok')\n", interval=60)
        with mock.patch.object(sync, "build_graph", side_effect=[(False, "graphify extract"), (True, "graphify extract")]) as build, quiet():
            first = sync.run(self.project, full=True)
            second = sync.run(self.project)
        self.assertTrue(first.errors)
        self.assertFalse(second.errors)
        self.assertTrue(second.graph_rebuilt)
        self.assertEqual(second.skipped, ["demo"])
        self.assertTrue(build.call_args.kwargs["full"])
        self.assertFalse((self.project / "connectors/.graph-pending.json").exists())

    def test_partial_connector_failure_still_indexes_published_objects(self):
        self.scaffold()
        self.write_connector("demo", "raise SystemExit(1)\n")
        with mock.patch.object(sync, "build_graph", return_value=(True, "graphify extract")) as build, quiet():
            report = sync.run(self.project)
        self.assertTrue(report.errors)
        self.assertTrue(report.graph_rebuilt)
        build.assert_called_once()
        self.assertTrue(sync.is_due(self.project, "demo", 60))

    def test_dry_run_does_not_clear_pending_indexing(self):
        self.scaffold()
        pending = self.project / "connectors/.graph-pending.json"
        pending.write_text('{"full":true}')
        with mock.patch.object(sync, "build_graph") as build, quiet():
            sync.run(self.project, dry_run=True)
        self.assertEqual(pending.read_text(), '{"full":true}')
        build.assert_not_called()


class AdditionalBoundaryTests(TempProjectTestCase):
    def test_parallel_sync_is_refused_without_running_a_connector(self):
        from brainiphy_cli.storage import exclusive_lock
        self.scaffold()
        self.write_connector("demo", "print('ok')\n")
        with exclusive_lock(self.project / "connectors/.sync.lock"), \
                mock.patch.object(sync.subprocess, "run") as runner, quiet():
            report = sync.run(self.project)
        self.assertTrue(report.errors)
        runner.assert_not_called()

    def test_shared_folder_rejects_cross_collector_id_collision(self):
        out = self.tmp / "out"
        first = collect.Collector("one", "schema", lambda: [record("same", detail="first")])
        second = collect.Collector("two", "schema", lambda: [record("same", detail="second")])
        with quiet():
            code = collect.run("crm", [first, second], argv=["--out", str(out)])
        self.assertEqual(code, 1)
        path = out / "schema" / frontmatter.record_filename("same")
        self.assertEqual(frontmatter.read_metadata(path)["detail"], "first")

    def test_tool_lookup_finds_mcp_in_the_running_virtualenv(self):
        fake_python = self.tmp / "venv/bin/python"
        mcp = fake_python.parent / "graphify-mcp"
        mcp.parent.mkdir(parents=True)
        mcp.write_text("#!/usr/bin/env python3\n")
        mcp.chmod(0o755)
        with mock.patch.object(sys, "executable", str(fake_python)), \
                mock.patch.object(project.shutil, "which", return_value=None):
            self.assertEqual(project.find_exe("graphify-mcp"), str(mcp))

    def test_corrupt_plist_does_not_break_status(self):
        folder = self.tmp / "Library/LaunchAgents"
        folder.mkdir(parents=True)
        (folder / "com.graphify.sync.broken.plist").write_text("<plist><dict><broken &")
        with mock.patch.object(Path, "home", return_value=self.tmp):
            self.assertIsNone(steps._launch_agent(self.project))

    def test_loaded_schedule_is_reloaded_after_an_interval_change(self):
        self.scaffold()
        self.write_connector("demo", "pass\n")
        result = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(Path, "home", return_value=self.tmp), \
                mock.patch.object(project, "find_exe", return_value="/bin/brain"), \
                mock.patch.object(project.subprocess, "run", return_value=result) as runner, quiet():
            self.assertTrue(project.schedule(self.project, interval_minutes=10, load=True))
        self.assertEqual([c.args[0][1] for c in runner.call_args_list], ["print", "bootout", "bootstrap"])
        plist = next((self.tmp / "Library/LaunchAgents").glob("*.plist"))
        self.assertEqual(plistlib.loads(plist.read_bytes())["StartInterval"], 600)


class InteractiveFlowTests(TempProjectTestCase):
    def test_app_adds_a_source_runs_real_mirror_and_returns_to_checklist(self):
        from brainiphy_cli import app, keys, picker, prompt, ui
        self.scaffold()
        source = self.tmp / "source"
        source.mkdir()
        (source / "note.md").write_text("# Source note\n")
        # Select sources, add local folder, pause, leave source menu, build,
        # pause after sync, then quit. Only graphify is replaced.
        keypresses = iter(["3", "enter", "enter", "enter", "esc", "5", "enter", "enter", "q"])
        answers = iter(["docs", "5"])
        with mock.patch.object(keys, "supported", return_value=True), \
                mock.patch.object(picker, "is_interactive", return_value=True), \
                mock.patch.object(picker, "pick_project_dir", return_value=source), \
                mock.patch.object(keys, "read_key", side_effect=lambda: next(keypresses)), \
                mock.patch.object(prompt, "ask", side_effect=lambda *a, **k: next(answers)), \
                mock.patch.object(prompt, "confirm", return_value=True), \
                mock.patch.object(ui, "clear"), \
                mock.patch.object(sync, "build_graph", return_value=(True, "graphify extract")), quiet():
            self.assertEqual(app.run(str(self.project)), 0)
        self.assertEqual((self.project / "raw/docs/note.md").read_text(), "# Source note\n")
        self.assertEqual(project.load_registry_entries(self.project)[0]["name"], "docs")

    def test_real_pty_reads_queued_arrows_and_restores_terminal(self):
        import termios
        import tty
        from brainiphy_cli import keys
        master, slave = os.openpty()
        saved = termios.tcgetattr(slave)
        stream = os.fdopen(os.dup(slave), "r")
        real_raw = tty.setraw
        pending = [b"\x1b[A\x1b[B"]
        def set_raw(fd, when):
            real_raw(fd, when)
            if pending:
                os.write(master, pending.pop())
        try:
            with mock.patch.object(sys, "stdin", stream), mock.patch.object(tty, "setraw", side_effect=set_raw):
                self.assertEqual(keys.read_key(), keys.UP)
                self.assertEqual(keys.read_key(), keys.DOWN)
            restored = termios.tcgetattr(slave)
            # macOS can set PENDIN when unread queued bytes cross a mode
            # change; it is a driver queue flag, not a mode left in raw state.
            restored[3] &= ~getattr(termios, "PENDIN", 0)
            saved[3] &= ~getattr(termios, "PENDIN", 0)
            self.assertEqual(restored, saved)
        finally:
            stream.close()
            os.close(master)
            os.close(slave)


class ExpandedAuditTests(TempProjectTestCase):
    def test_corrupt_brain_registry_is_not_overwritten_by_registration(self):
        from brainiphy_cli import brains
        path = brains.registry_path()
        path.parent.mkdir(parents=True)
        for payload in ("brains: [unclosed", "- not-a-mapping", "brains: unexpected-string"):
            with self.subTest(payload=payload):
                path.write_text(payload)
                with self.assertRaises(ValueError):
                    brains.remember(self.project)
                self.assertEqual(path.read_text(), payload)

    def test_invalid_graph_shape_is_reported_as_unbuilt(self):
        self.scaffold()
        graph = self.write_graph()
        for payload in ("[1]", '{"nodes":null}', '{"nodes":[],"links":4}'):
            with self.subTest(payload=payload):
                graph.write_text(payload)
                state = steps.inspect(self.project)
                self.assertFalse(next(s for s in state.steps if s.key == "build").done)

    def test_url_content_settles_the_source_step_before_indexing(self):
        self.scaffold()
        raw = self.project / "raw/web"
        raw.mkdir(parents=True)
        (raw / "page.md").write_text("# Fetched URL\n")
        self.assertTrue(next(s for s in steps.inspect(self.project).steps if s.key == "sources").done)

    def test_missing_script_menu_returns_without_a_traceback(self):
        from brainiphy_cli import app
        self.scaffold()
        project.write_registry_entries(self.project, [{"name": "missing"}])
        with mock.patch.object(app, "_choose", side_effect=[0, None]), \
                mock.patch.object(app, "_pause"), quiet() as output:
            app._do_implement(self.project)
        self.assertIn("restore the connector", output.getvalue())

    def test_documented_path_shortcut_reaches_the_app(self):
        from brainiphy_cli import app, cli
        with mock.patch.object(sys, "argv", ["brain", str(self.project)]), \
                mock.patch.object(app, "run", return_value=0) as opened:
            self.assertEqual(cli.main(), 0)
        opened.assert_called_once_with(str(self.project))

    def test_mirror_conversion_failure_preserves_previous_records_and_returns_error(self):
        from brainiphy_cli import mirror_template as mirror
        source = self.tmp / "source"
        source.mkdir()
        data = source / "export.json"
        data.write_text('[{"name":"Acme"}]')
        out = self.tmp / "out"
        with mock.patch.object(mirror, "MIRROR_SOURCE", source), \
                mock.patch.object(sys, "argv", ["sync.py", "--out", str(out)]), quiet():
            self.assertEqual(mirror.main(), 0)
            before = {p.relative_to(out): (p.read_bytes(), p.stat().st_mtime_ns) for p in (out / "_converted").rglob("*.md")}
            self.assertEqual(len(before), 1)
            self.assertEqual(mirror.main(), 0)
            for path, (content, stamp) in before.items():
                self.assertEqual((out / path).read_bytes(), content)
                self.assertEqual((out / path).stat().st_mtime_ns, stamp)
            data.write_text("{not json")
            self.assertEqual(mirror.main(), 1)
            for path, (content, _) in before.items():
                self.assertEqual((out / path).read_bytes(), content)
            data.write_text("[]")
            self.assertEqual(mirror.main(), 0)
            self.assertEqual(list((out / "_converted").rglob("*.md")), [])


class EncodingAndResponseTests(TempProjectTestCase):
    def test_every_single_byte_codepoint_survives_yaml_round_trip(self):
        import yaml
        for point in range(256):
            with self.subTest(point=point):
                value = chr(point)
                parsed = yaml.safe_load('value: "' + frontmatter.yaml_str(value) + '"')
                self.assertEqual(parsed["value"], value)

    def test_yaml_reserved_words_remain_string_field_names(self):
        extras = {name: "value" for name in ("yes", "no", "true", "false", "null", "on", "off")}
        path = frontmatter.write_record(self.tmp, record_id="1", title="title", body="body", source_system="s", extra_fields=extras)
        data = frontmatter.read_metadata(path)
        for key in extras:
            self.assertEqual(data[key], "value")

    def test_non_paginated_missing_collection_is_not_an_empty_snapshot(self):
        client = httpclient.HttpClient("https://api.example.com", token="synthetic")
        for response in ({}, {"users": None}, {"users": {}}, []):
            with self.subTest(response=response), mock.patch.object(client, "get", return_value=response), self.assertRaises(httpclient.IncompleteFetch):
                client.get_list("/users", "users")
        with mock.patch.object(client, "get", return_value={"users": []}):
            self.assertEqual(client.get_list("/users", "users"), [])


class LegacyCompatibilityTests(TempProjectTestCase):
    def test_old_collector_retains_deleted_records_until_snapshot_is_opted_in(self):
        out = self.tmp / "out"
        collector = collect.Collector("contacts", "contacts", lambda: [record("1"), record("2")])
        with quiet():
            self.assertEqual(collect.run("crm", [collector], argv=["--out", str(out)]), 0)
            collector.fetch = lambda: [record("1")]
            self.assertEqual(collect.run("crm", [collector], argv=["--out", str(out)]), 0)
            self.assertEqual(len(list(out.rglob("*.md"))), 2)
            collector.snapshot = True
            self.assertEqual(collect.run("crm", [collector], argv=["--out", str(out)]), 0)
        self.assertEqual(len(list(out.rglob("*.md"))), 1)

    def test_legacy_filename_migration_does_not_require_snapshot_deletion(self):
        out = self.tmp / "out"
        out.mkdir()
        legacy = out / "old.md"
        legacy.write_text('---\nsource_id: old\nsource_system: crm\n---\nold\n')
        collector = collect.Collector("records", ".", lambda: [record("old")])
        with quiet():
            self.assertEqual(collect.run("crm", [collector], argv=["--out", str(out)]), 0)
        self.assertFalse(legacy.exists())
        self.assertEqual(len(list(out.glob("*.md"))), 1)


class ManifestLocationTests(TempProjectTestCase):
    def test_moving_a_collector_cannot_prune_files_owned_in_another_folder(self):
        out = self.tmp / "out"
        one = collect.Collector("one", "first", lambda: [record("old")], snapshot=True)
        two = collect.Collector("two", "second", lambda: [record("old")], snapshot=True)
        with quiet():
            self.assertEqual(collect.run("crm", [one, two], argv=["--out", str(out)]), 0)
            one.subfolder = "second"
            one.fetch = lambda: [record("new")]
            self.assertEqual(collect.run("crm", [one, two], argv=["--out", str(out), "--only", "one"]), 1)
        self.assertTrue((out / "second" / frontmatter.record_filename("old")).exists())
        self.assertTrue((out / "first" / frontmatter.record_filename("old")).exists())


class MovedScheduleTests(TempProjectTestCase):
    def test_rescheduling_a_moved_brain_keeps_its_job_and_updates_the_path(self):
        self.scaffold()
        self.write_connector("demo", "pass\n")
        with mock.patch.object(Path, "home", return_value=self.tmp), \
                mock.patch.object(project, "find_exe", return_value="/bin/brain"), quiet():
            self.assertTrue(project.schedule(self.project, slug_override="legacy-job"))
            old_plist = project.launch_agent(self.project)
            moved = self.tmp / "moved-brain"
            self.project.rename(moved)
            self.assertIsNone(project.launch_agent(moved))
            self.assertTrue(project.schedule(moved))
            self.assertEqual(project.launch_agent(moved), old_plist)
            self.assertEqual(plistlib.loads(old_plist.read_bytes())["ProgramArguments"][2], str(moved))
