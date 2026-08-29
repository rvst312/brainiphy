"""The credential boundary, exercised without touching the real Keychain.

The rule the whole module exists to enforce: a secret value never travels as a
process argument. Arguments are readable by any process running as the same
user for as long as the command runs, and they are also what lands in shell
history and launchd logs. `security` agrees — "Use of the -p or -w options is
insecure. Specify -w as the last option to be prompted."

set_secret used to pass `-w <value>` and so broke the rule this module is for.
It now writes the value to `security`'s stdin, and these tests are what stop it
from drifting back.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brainiphy_cli import keychain  # noqa: E402


class GetSecretTests(unittest.TestCase):
    def test_the_value_is_read_by_item_name_alone(self):
        # The request carries the *name* of a credential and nothing else —
        # this is what keeps a token out of shell history and process listings.
        with mock.patch.object(keychain.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout="tok\n")) as run:
            value = keychain.get_secret("graphify-acme-crm")
        self.assertEqual(value, "tok")
        argv = run.call_args[0][0]
        self.assertEqual(argv[:2], ["security", "find-generic-password"])
        self.assertIn("graphify-acme-crm", argv)

    def test_trailing_whitespace_is_stripped(self):
        # `security` prints a newline; sent as a Bearer token it becomes an
        # invalid header and a 401 that looks like a wrong key.
        with mock.patch.object(keychain.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout="  tok  \n")):
            self.assertEqual(keychain.get_secret("item"), "tok")

    def test_a_missing_item_explains_how_to_register_it(self):
        with mock.patch.object(keychain.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stdout="", stderr="")):
            with self.assertRaises(keychain.SecretNotFoundError) as caught:
                keychain.get_secret("graphify-acme-crm")
        message = str(caught.exception)
        self.assertIn("add-generic-password", message)
        self.assertIn("graphify-acme-crm", message)

    def test_the_error_is_a_runtime_error_a_connector_can_catch(self):
        self.assertTrue(issubclass(keychain.SecretNotFoundError, RuntimeError))


class SetSecretTests(unittest.TestCase):
    """Storing a credential, with `security` replaced."""

    def _store(self, value="tok3n", *, returncode=0, stderr="", readback=None,
               found=True):
        """Run set_secret with the subprocess and the read-back stubbed, and
        return the call `security` would have received.

        readback defaults to the value itself — a Keychain that works."""
        run = mock.Mock(return_value=mock.Mock(
            returncode=returncode, stdout="", stderr=stderr))
        stored = value.strip() if readback is None else readback
        get = (mock.Mock(return_value=stored) if found
               else mock.Mock(side_effect=keychain.SecretNotFoundError("gone")))
        with mock.patch.object(keychain.subprocess, "run", run), \
                mock.patch.object(keychain, "get_secret", get):
            keychain.set_secret("graphify-acme-crm", value)
        return run.call_args

    def test_the_value_is_never_a_process_argument(self):
        """The non-negotiable one.

        `ps` shows another process's arguments to the same user, so a token in
        argv is readable for as long as `security` runs — and it is in shell
        history and launchd logs afterwards.
        """
        call = self._store("super-secret-token")
        argv = call[0][0]
        self.assertNotIn("super-secret-token", argv)
        # Not hidden inside a joined argument either.
        self.assertNotIn("super-secret-token", " ".join(argv))

    def test_the_value_is_written_to_stdin_twice(self):
        # `security` asks for the value and then for a confirmation; one line
        # leaves it waiting, and a mismatch is not reported as an error.
        call = self._store("tok3n")
        self.assertEqual(call[1]["input"], "tok3n\ntok3n\n")

    def test_the_bare_w_flag_comes_last(self):
        # This is what makes `security` read from stdin at all. Anything after
        # it would be taken as the password.
        argv = self._store()[0][0]
        self.assertEqual(argv[-1], "-w")
        self.assertIn("-U", argv)          # update an existing item in place

    def test_a_failing_security_call_is_raised_not_swallowed(self):
        with self.assertRaises(keychain.SecretWriteError):
            self._store(returncode=1, stderr="keychain is locked")

    def test_success_is_confirmed_by_reading_the_value_back(self):
        """`security` exits 0 on paths that store nothing.

        Feed it a value and a confirmation that disagree and it returns 0
        having stored something else — verified against a throwaway keychain.
        So the exit code alone must never be reported to the user as success.
        """
        with self.assertRaises(keychain.SecretWriteError):
            self._store(found=False)

    def test_a_value_that_comes_back_different_is_an_error(self):
        with self.assertRaises(keychain.SecretWriteError):
            self._store("sent", readback="something else")

    def test_surrounding_whitespace_still_counts_as_a_match(self):
        # get_secret() strips, so the read-back has to be compared against a
        # stripped value or every padded token would look like a failure.
        self._store("  tok3n  ", readback="tok3n")

    def test_a_value_with_a_line_break_is_refused_before_anything_runs(self):
        # It would arrive as two disagreeing answers, which `security` does not
        # report as an error. A pasted token with a stray newline is the case.
        with mock.patch.object(keychain.subprocess, "run") as run:
            with self.assertRaises(keychain.SecretWriteError):
                keychain.set_secret("item", "tok3n\nmore")
        run.assert_not_called()

    def test_an_empty_value_is_refused(self):
        with mock.patch.object(keychain.subprocess, "run") as run:
            with self.assertRaises(keychain.SecretWriteError):
                keychain.set_secret("item", "")
        run.assert_not_called()

    def test_the_write_error_is_a_runtime_error(self):
        self.assertTrue(issubclass(keychain.SecretWriteError, RuntimeError))


class DocumentedCommandTests(unittest.TestCase):
    def test_the_command_we_tell_users_to_run_is_the_safe_one(self):
        """The error message is copy-pasted into a shell, so it must not teach
        the pattern the module refuses to use itself."""
        with mock.patch.object(keychain.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stdout="", stderr="")):
            with self.assertRaises(keychain.SecretNotFoundError) as caught:
                keychain.get_secret("item")
        message = str(caught.exception)
        self.assertIn("-w", message)
        # A value after -w is what puts it in shell history.
        self.assertNotIn("-w '<value>'", message)
        self.assertNotIn("-w <value>", message)


if __name__ == "__main__":
    unittest.main()
