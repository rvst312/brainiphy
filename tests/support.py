"""Shared helpers for the unit suite.

Plain `unittest` on purpose: the house rule is no new tooling without a
reason, and the suite has to run on the same interpreter as the package under
three CI matrix entries without adding a dependency to install first.
"""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path


@contextlib.contextmanager
def quiet():
    """Swallow everything the module under test prints, and hand it back.

    Every operation in project.py/sync.py reports its own progress through
    `ui`, which is the right behavior for a CLI and pure noise in a test run.
    Rich resolves sys.stdout at write time, so redirecting is enough — no need
    to reach into the Console objects.

    Yields the buffer, so a test that cares about the *message* (the no-key
    hint, a warning about an unknown constant) can assert on it.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield buf


class TempProjectTestCase(unittest.TestCase):
    """A throwaway directory per test, cleaned up whatever happens.

    BRAINIPHY_HOME is redirected into it for every test, not just the ones
    about the brains list: scaffolding a project registers it, so without this
    a plain test run would quietly append temp folders to the developer's own
    list of brains.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.project = self.tmp / "acme-brain"

        patcher = mock.patch.dict(os.environ, {"BRAINIPHY_HOME": str(self.tmp / "config")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def scaffold(self) -> Path:
        """A project as `brain init` leaves it."""
        from brainiphy_cli import project as project_mod

        with quiet():
            project_mod.scaffold(self.project)
        return self.project

    def write_connector(self, name: str, body: str, *, register: bool = True,
                        type_: str = "custom", interval: float = 60) -> Path:
        """Put a connector on disk (and in the registry) without running the
        real template machinery — for tests about *detection*, not creation."""
        from brainiphy_cli import project as project_mod

        script = self.project / "connectors" / name / "sync.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(body, encoding="utf-8")
        if register:
            entries = project_mod.load_registry_entries(self.project)
            entries.append({"name": name, "type": type_, "interval_minutes": interval})
            project_mod.write_registry_entries(self.project, entries)
        return script

    def write_graph(self, nodes: int = 3, edges: int = 2) -> Path:
        """A graphify-out/graph.json of a given size, so steps.py sees a built
        graph without anything having to run."""
        import json

        graph = self.project / "graphify-out" / "graph.json"
        graph.parent.mkdir(parents=True, exist_ok=True)
        graph.write_text(json.dumps({
            "nodes": [{"id": f"n{i}"} for i in range(nodes)],
            "links": [{"source": "n0", "target": "n1"} for _ in range(edges)],
        }), encoding="utf-8")
        return graph
