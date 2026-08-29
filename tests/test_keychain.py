"""The credential boundary, exercised without touching the real Keychain.

`get_secret` is the only call a connector is allowed to make, so the two things
worth pinning are that it asks for the item by name (never carries a value) and
that a missing item produces the command to fix it rather than a
CalledProcessError from three frames down.
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


if __name__ == "__main__":
    unittest.main()
