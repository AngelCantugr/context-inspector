"""CLI smoke tests for the weekly subcommand (#17).

The pipeline stages are stubbed at their module seams
(``context_inspector.weekly.run_weekly`` and
``context_inspector.agent.graph.run_interpretation``) so the tests exercise
the CLI wiring — preflight order, --dry-run stdout/filing behavior — with
no sweep, no agent extras beyond import, and no API calls.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from context_inspector import cli

MANIFEST = {"generated": "2026-10-15", "baseline": True, "outliers": []}


def _env(**overrides: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.pop(cli.VAULT_ENV_VAR, None)
    env.update(overrides)
    return env


class _FakeInterpretation:
    """Stand-in for run_interpretation: records config, returns a memo."""

    def __init__(self, memo):
        self.memo = memo
        self.calls: list = []

    def __call__(self, manifest, config):
        self.calls.append((manifest, config))
        return self.memo


def _memo(filed_path: str = ""):
    from context_inspector.agent import Memo

    return Memo(week_id="2026-W42", date="2026-10-15", baseline=True,
                filed_path=filed_path)


class WeeklyPreflightTest(unittest.TestCase):
    def test_missing_api_key_fails_before_any_pipeline_work(self):
        with mock.patch("context_inspector.weekly.run_weekly") as sweep, \
            mock.patch(
                "context_inspector.agent.graph.run_interpretation"
            ) as interpret, \
            mock.patch.dict(os.environ, _env(), clear=True), \
            redirect_stderr(io.StringIO()) as err:
            rc = cli.main(["weekly"])
        self.assertEqual(rc, 1)
        self.assertIn("ANTHROPIC_API_KEY", err.getvalue())
        sweep.assert_not_called()
        interpret.assert_not_called()

    def test_missing_vault_path_fails_before_any_pipeline_work(self):
        with mock.patch("context_inspector.weekly.run_weekly") as sweep, \
            mock.patch(
                "context_inspector.agent.graph.run_interpretation"
            ) as interpret, \
            mock.patch.dict(
                os.environ, _env(ANTHROPIC_API_KEY="sk-test"), clear=True
            ), \
            redirect_stderr(io.StringIO()) as err:
            rc = cli.main(["weekly"])
        self.assertEqual(rc, 1)
        self.assertIn(cli.VAULT_ENV_VAR, err.getvalue())
        sweep.assert_not_called()
        interpret.assert_not_called()

    def test_dry_run_does_not_require_vault_path(self):
        fake = _FakeInterpretation(_memo())
        with mock.patch("context_inspector.weekly.run_weekly",
                        return_value=MANIFEST) as sweep, \
            mock.patch(
                "context_inspector.agent.graph.run_interpretation", fake
            ), \
            mock.patch.dict(
                os.environ, _env(ANTHROPIC_API_KEY="sk-test"), clear=True
            ), \
            redirect_stdout(io.StringIO()):
            rc = cli.main(["weekly", "--dry-run"])
        self.assertEqual(rc, 0)
        sweep.assert_called_once_with()
        _, config = fake.calls[0]
        self.assertTrue(config.dry_run)
        self.assertIsNone(config.notes_dir)


class WeeklyDryRunTest(unittest.TestCase):
    def test_dry_run_prints_memo_and_never_touches_vault(self):
        from context_inspector.agent import render_markdown

        memo = _memo()
        fake = _FakeInterpretation(memo)
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            with mock.patch("context_inspector.weekly.run_weekly",
                            return_value=MANIFEST), \
                mock.patch(
                    "context_inspector.agent.graph.run_interpretation", fake
                ), \
                mock.patch.dict(os.environ, _env(
                    ANTHROPIC_API_KEY="sk-test",
                    **{cli.VAULT_ENV_VAR: str(vault)},
                ), clear=True), \
                redirect_stdout(io.StringIO()) as out:
                rc = cli.main(["weekly", "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertEqual(out.getvalue(), render_markdown(memo) + "\n")
            self.assertFalse(vault.exists(), "--dry-run must not touch the vault")
            _, config = fake.calls[0]
            self.assertTrue(config.dry_run)

    def test_real_run_files_note_and_reports_path(self):
        filed = "/vault/reports/context-hygiene-weekly-2026-W42.md"
        fake = _FakeInterpretation(_memo(filed_path=filed))
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            with mock.patch("context_inspector.weekly.run_weekly",
                            return_value=MANIFEST), \
                mock.patch(
                    "context_inspector.agent.graph.run_interpretation", fake
                ), \
                mock.patch.dict(os.environ, _env(
                    ANTHROPIC_API_KEY="sk-test",
                    **{cli.VAULT_ENV_VAR: str(vault)},
                ), clear=True), \
                redirect_stdout(io.StringIO()) as out:
                rc = cli.main(["weekly"])
            self.assertEqual(rc, 0)
            self.assertIn(filed, out.getvalue())
            _, config = fake.calls[0]
            self.assertFalse(config.dry_run)
            self.assertEqual(config.notes_dir, vault / "reports")


if __name__ == "__main__":
    unittest.main()
