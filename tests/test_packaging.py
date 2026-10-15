"""Packaging invariants (#13): uv + hatchling migration.

- Build backend is hatchling.
- The `agent` extra pins langgraph + anthropic (imported only inside
  `context_inspector.agent`, which does not exist yet).
- Dev dependencies live in `[dependency-groups].dev`.
- `uv.lock` is committed and `.python-version` is pinned.
- The base package imports no third-party modules at startup.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

THIRD_PARTY = {"duckdb", "matplotlib", "tiktoken", "langgraph", "anthropic"}


def _project():
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


class BuildBackendTest(unittest.TestCase):
    def test_build_backend_is_hatchling(self):
        build = _project()["build-system"]
        self.assertEqual(build["build-backend"], "hatchling.build")
        self.assertIn("hatchling", build["requires"])


class AgentExtraTest(unittest.TestCase):
    def test_agent_extra_pins_langgraph_and_anthropic(self):
        extras = _project()["project"]["optional-dependencies"]
        deps = extras["agent"]
        self.assertTrue(any(d.startswith("langgraph") for d in deps), deps)
        self.assertTrue(any(d.startswith("anthropic") for d in deps), deps)

    def test_analytics_and_tiktoken_extras_unchanged(self):
        extras = _project()["project"]["optional-dependencies"]
        self.assertTrue(any(d.startswith("duckdb") for d in extras["analytics"]))
        self.assertTrue(any(d.startswith("matplotlib") for d in extras["analytics"]))
        self.assertTrue(any(d.startswith("tiktoken") for d in extras["tiktoken"]))


class DevGroupTest(unittest.TestCase):
    def test_dev_dependencies_in_dependency_group(self):
        dev = _project()["dependency-groups"]["dev"]
        names = {d.split(">")[0].split("=")[0].strip() for d in dev}
        self.assertIn("duckdb", names)
        self.assertIn("matplotlib", names)


class LockAndPinTest(unittest.TestCase):
    def test_uv_lock_committed(self):
        lock = ROOT / "uv.lock"
        self.assertTrue(lock.is_file(), "uv.lock must exist at the repo root")
        text = lock.read_text(encoding="utf-8")
        self.assertIn("context-inspector", text)

    def test_python_version_pinned(self):
        pin = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
        self.assertTrue(pin, ".python-version must not be empty")
        requires = _project()["project"]["requires-python"]
        major, minor = int(pin.split(".")[0]), int(pin.split(".")[1])
        self.assertGreaterEqual((major, minor), (3, 10), f"{pin} violates {requires}")


def _executed_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """(level, dotted-name) imports executed at module-import time.

    Imports inside functions are lazy (allowed), and ``if TYPE_CHECKING:``
    blocks never run, so neither is scanned.
    """
    found: list[tuple[int, str]] = []
    for stmt in tree.body:
        targets: list[ast.stmt] = []
        if isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Name):
            if stmt.test.id != "TYPE_CHECKING":
                targets.extend(stmt.body)
        else:
            targets.append(stmt)
        for node in targets:
            if isinstance(node, ast.Import):
                found.extend((0, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    found.append((node.level, node.module))
                else:
                    # `from . import x` — each alias is a submodule at the
                    # same relative level and must be walked too
                    found.extend((node.level, alias.name) for alias in node.names)
    return found


class StdlibOnlyStartupTest(unittest.TestCase):
    def test_cli_import_closure_has_no_third_party_modules(self):
        # Walk the static import graph starting at context_inspector.cli and
        # assert no third-party package is reachable at import time. Optional
        # surfaces (analytics, charts) stay lazy inside functions, and
        # langgraph/anthropic may only ever appear under context_inspector.agent.
        pkg_dir = ROOT / "context_inspector"
        visited: set[str] = set()
        queue = ["context_inspector.cli"]
        while queue:
            mod = queue.pop()
            if mod in visited:
                continue
            visited.add(mod)
            rel = mod.split(".")[1:]
            path = pkg_dir.joinpath(*rel)
            init = path / "__init__.py"
            is_package = init.is_file()
            path = init if is_package else path.with_suffix(".py")
            self.assertTrue(path.is_file(), f"cannot resolve module {mod}")
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for level, name in _executed_imports(tree):
                parts = name.split(".")
                if parts[0] in THIRD_PARTY:
                    self.fail(
                        f"{path.relative_to(ROOT)} imports third-party "
                        f"module '{name}' at module level"
                    )
                if parts[0] == "context_inspector":
                    queue.append(name)
                elif level:
                    # relative import: level 1 anchors at the current package —
                    # the module itself if it is a package, else its parent
                    drop = level - 1 if is_package else level
                    base = mod.split(".")[:-drop] if drop else mod.split(".")
                    queue.append(".".join(base + parts))
        self.assertIn("context_inspector.cli", visited)

    def test_langgraph_anthropic_only_inside_agent_package(self):
        # Until context_inspector.agent exists, nothing in the package may
        # import langgraph or anthropic at all.
        for path in sorted((ROOT / "context_inspector").rglob("*.py")):
            relative = path.relative_to(ROOT / "context_inspector")
            if relative.parts[0] == "agent":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("langgraph", text, str(relative))
            self.assertNotIn("anthropic", text, str(relative))

    def test_base_cli_importable_without_third_party_packages(self):
        # Block third-party imports, then import the CLI entry point exactly as
        # the console script does. Anything lazy (duckdb, matplotlib, tiktoken)
        # must stay out of the startup path.
        code = (
            "import sys\n"
            f"BLOCKED = {set(sorted(THIRD_PARTY))!r}\n"
            "class Blocker:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in BLOCKED:\n"
            "            raise ImportError(f'blocked third-party import: {name}')\n"
            "        return None\n"
            "sys.meta_path.insert(0, Blocker())\n"
            # Probe with a stdlib module that a fresh interpreter has not
            # imported yet: if find_spec is honored it raises, and if some
            # future interpreter ignores the protocol the probe succeeds and
            # we exit non-zero instead of passing with an inert blocker.
            "BLOCKED.add('json')\n"
            "try:\n"
            "    import json\n"
            "    print('blocker inert')\n"
            "    sys.exit(1)\n"
            "except ImportError:\n"
            "    pass\n"
            "BLOCKED.discard('json')\n"
            "from context_inspector.cli import main\n"
            "print('ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
