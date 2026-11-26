"""Exercise profile filtering without running installation or changing HOME."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import TempProjectTestCase


class ProfileRemovalTests(TempProjectTestCase):
    def remove_block(self, content):
        profile = self.tmp / "profile"
        profile.write_text(content)
        installer = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        start = installer.index("remove_from_path() {")
        end = installer.index("\n# ------------------------------------------------------------- uninstall", start)
        script = '''
set -euo pipefail
DRY_RUN=0
MARKER_OPEN='# >>> brainiphy >>>'
MARKER_CLOSE='# <<< brainiphy <<<'
profile_file() { printf '%s' "$AUDIT_PROFILE"; }
info() { :; }
ok() { :; }
die() { exit 1; }
'''+installer[start:end]+"\nremove_from_path\n"
        result = subprocess.run(["bash", "-c", script],
                                env=dict(os.environ, AUDIT_PROFILE=str(profile)), capture_output=True, text=True)
        return result.returncode, profile.read_text()

    def test_complete_block_is_removed_and_surrounding_profile_is_preserved(self):
        status, content = self.remove_block("export EDITOR=vim\n# >>> brainiphy >>>\nexport PATH=tool\n# <<< brainiphy <<<\nexport KEEP=1\n")
        self.assertEqual(status, 0)
        self.assertEqual(content, "export EDITOR=vim\nexport KEEP=1\n")

    def test_unclosed_or_nested_marker_never_truncates_the_profile(self):
        for content in ("export EDITOR=vim\n# >>> brainiphy >>>\nexport KEEP=1\n",
                        "# >>> brainiphy >>>\n# >>> brainiphy >>>\n# <<< brainiphy <<<\nexport KEEP=1\n"):
            with self.subTest(content=content):
                status, after = self.remove_block(content)
                self.assertNotEqual(status, 0)
                self.assertEqual(after, content)
