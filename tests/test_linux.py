"""The Linux family: credentials as owner-only files, the schedule as a
systemd timer. Everything runs against temporary folders; systemctl is never
called.
"""
from __future__ import annotations

import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brainiphy_cli import hostos, keychain, project as project_mod, systemd  # noqa: E402
from support import TempProjectTestCase, quiet  # noqa: E402


def linux():
    return mock.patch.dict(os.environ, {"BRAINIPHY_BACKEND": "linux"})


class HostTests(unittest.TestCase):
    def test_the_override_wins_over_the_real_platform(self):
        with linux():
            self.assertFalse(hostos.is_macos())
        with mock.patch.dict(os.environ, {"BRAINIPHY_BACKEND": "macos"}):
            self.assertTrue(hostos.is_macos())


class FileSecretTests(TempProjectTestCase):
    def setUp(self):
        super().setUp()
        self.folder = self.tmp / "secrets"
        patcher = mock.patch.dict(os.environ, {"BRAINIPHY_SECRETS_DIR": str(self.folder)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_value_round_trips_without_touching_the_keychain(self):
        with mock.patch.object(keychain.subprocess, "run") as run:
            keychain.set_secret("graphify-acme-crm", "tok-123")
            self.assertEqual(keychain.get_secret("graphify-acme-crm"), "tok-123")
        run.assert_not_called()

    def test_only_the_owner_can_read_what_is_stored(self):
        keychain.set_secret("item", "tok")
        self.assertEqual(stat.S_IMODE((self.folder / "item").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)

    def test_a_file_other_users_can_read_is_refused(self):
        keychain.set_secret("item", "tok")
        os.chmod(self.folder / "item", 0o644)
        with self.assertRaises(keychain.SecretNotFoundError):
            keychain.get_secret("item")

    def test_a_missing_item_names_the_command_that_registers_it(self):
        with self.assertRaises(keychain.SecretNotFoundError) as caught:
            keychain.get_secret("graphify-acme-crm")
        self.assertIn("brain secret set graphify-acme-crm", str(caught.exception))

    def test_a_name_cannot_leave_the_folder(self):
        for name in ("../escape", "a/b", ".hidden", ""):
            with self.subTest(name=name):
                with self.assertRaises(keychain.SecretWriteError):
                    keychain.set_secret(name, "tok")
                with self.assertRaises(keychain.SecretNotFoundError):
                    keychain.get_secret(name)
        self.assertFalse((self.tmp / "escape").exists())

    def test_linux_defaults_to_a_folder_and_macos_to_the_keychain(self):
        with mock.patch.dict(os.environ, {"BRAINIPHY_SECRETS_DIR": ""}):
            with linux():
                self.assertIsNotNone(keychain.secrets_dir())
            with mock.patch.dict(os.environ, {"BRAINIPHY_BACKEND": "macos"}):
                self.assertIsNone(keychain.secrets_dir())


class SystemdScheduleTests(TempProjectTestCase):
    def setUp(self):
        super().setUp()
        self.units = self.tmp / "units"
        for patcher in (
            linux(),
            mock.patch.object(systemd, "unit_dir", return_value=self.units),
            mock.patch.object(systemd, "is_system", return_value=False),
            mock.patch.object(project_mod, "find_exe", return_value="/opt/venv/bin/brain"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        with quiet():
            project_mod.scaffold(self.project)
            project_mod.create_connector(self.project, "docs", interval_minutes=60)

    def _schedule(self, project=None, **kw):
        with quiet():
            return project_mod.schedule(project or self.project, **kw)

    def test_it_writes_a_service_and_a_timer_and_no_plist(self):
        self.assertTrue(self._schedule(interval_minutes=30))
        timer = project_mod.launch_agent(self.project)
        self.assertIsNotNone(timer)
        service = timer.with_suffix(".service").read_text()
        self.assertIn(f'ExecStart="/opt/venv/bin/brain" sync "{self.project}" --full', service)
        self.assertIn("OnUnitInactiveSec=1800s", timer.read_text())
        self.assertNotIn("User=", service)
        self.assertFalse((self.tmp / "Library").exists())

    def test_a_closed_volume_skips_the_run_instead_of_failing_it(self):
        self._schedule()
        service = project_mod.launch_agent(self.project).with_suffix(".service").read_text()
        self.assertIn(f"ConditionPathExists={self.project}/connectors/registry.yaml", service)

    def test_rescheduling_replaces_the_job_instead_of_adding_one(self):
        self._schedule(interval_minutes=15)
        self._schedule(interval_minutes=60)
        self.assertEqual(len(list(self.units.glob("*.timer"))), 1)
        self.assertIn("OnUnitInactiveSec=3600s", next(self.units.glob("*.timer")).read_text())

    def test_another_project_cannot_take_over_the_name(self):
        self._schedule(slug_override="shared")
        other = self.tmp / "other"
        with quiet():
            project_mod.scaffold(other)
            project_mod.create_connector(other, "docs", interval_minutes=60)
        self.assertFalse(self._schedule(other, slug_override="shared"))
        self.assertIsNone(project_mod.launch_agent(other))

    def test_as_root_the_sync_runs_as_the_owner_of_the_brain(self):
        owner = mock.Mock(pw_name="cerebro")
        with mock.patch.object(systemd, "is_system", return_value=True), \
                mock.patch.object(systemd.pwd, "getpwuid", return_value=owner), \
                mock.patch.object(systemd.shutil, "chown"):
            self.assertTrue(self._schedule())
        service = next(self.units.glob("*.service")).read_text()
        self.assertIn("User=cerebro", service)

    def test_as_root_a_brain_owned_by_root_is_refused(self):
        with mock.patch.object(systemd, "is_system", return_value=True), \
                mock.patch.object(systemd.pwd, "getpwuid", return_value=mock.Mock(pw_name="root")):
            self.assertFalse(self._schedule())
        self.assertEqual(list(self.units.glob("*")), [])

    def test_paths_with_spaces_and_specifiers_survive_the_exec_line(self):
        self.assertEqual(systemd._quote("/srv/Acme 100%/$x"), '"/srv/Acme 100%%/$$x"')

    def test_load_reloads_systemd_and_enables_the_timer(self):
        ok = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(systemd.subprocess, "run", return_value=ok) as run:
            self.assertTrue(self._schedule(load=True))
        calls = [c.args[0] for c in run.call_args_list]
        self.assertEqual(calls[0], ["systemctl", "--user", "daemon-reload"])
        self.assertEqual(calls[1][:4], ["systemctl", "--user", "enable", "--now"])


if __name__ == "__main__":
    unittest.main()
