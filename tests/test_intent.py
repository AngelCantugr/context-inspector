"""Rule-based tool-intent classifier tests (gh#12)."""

from __future__ import annotations

import json
import unittest

from context_inspector.intent import INTENT_VERSION, classify


class ClassifyTest(unittest.TestCase):
    def test_version_is_dated(self):
        self.assertRegex(INTENT_VERSION, r"^\d{4}-\d{2}-\d{2}\.v\d+$")

    def test_exact_tool_names(self):
        self.assertEqual(classify("Read", ""), "explore")
        self.assertEqual(classify("Grep", ""), "explore")
        self.assertEqual(classify("Glob", ""), "explore")
        self.assertEqual(classify("WebFetch", ""), "explore")
        self.assertEqual(classify("WebSearch", ""), "explore")
        self.assertEqual(classify("Edit", ""), "mutate")
        self.assertEqual(classify("Write", ""), "mutate")
        self.assertEqual(classify("apply_patch", ""), "mutate")
        self.assertEqual(classify("Agent", ""), "delegate")
        self.assertEqual(classify("SendMessage", ""), "delegate")
        self.assertEqual(classify("TodoWrite", ""), "meta")
        self.assertEqual(classify("TaskCreate", ""), "meta")

    def test_serena_exact_and_prefix(self):
        for name in (
            "mcp__serena__find_symbol",
            "mcp__serena__get_symbols_overview",
            "mcp__serena__search_for_pattern",
            "mcp__serena__read_file",
        ):
            self.assertEqual(classify(name, ""), "explore")
        # unknown serena tool still lands in explore via prefix
        self.assertEqual(classify("mcp__serena__rename_symbol", ""), "explore")
        # unknown MCP server is NOT explore by default
        self.assertEqual(classify("mcp__other__tool", ""), "other")

    def test_repl_is_never_shell_parsed(self):
        self.assertEqual(classify("js_repl", '{"command": "cat x.py"}'), "repl")

    def test_shell_command_heads(self):
        for head, intent in [
            ("cat", "explore"), ("ls", "explore"), ("rg", "explore"),
            ("grep", "explore"), ("sed", "explore"), ("head", "explore"),
            ("tail", "explore"), ("find", "explore"), ("less", "explore"),
            ("file", "explore"), ("wc", "explore"), ("diff", "explore"),
            ("stat", "explore"), ("tree", "explore"), ("jq", "explore"),
            ("pytest", "test-build"), ("make", "test-build"),
            ("npm", "test-build"), ("npx", "test-build"),
            ("cargo", "test-build"), ("go", "test-build"),
            ("cmake", "test-build"), ("tsc", "test-build"),
            ("build", "test-build"),
            ("git", "vcs"), ("gh", "vcs"),
        ]:
            with self.subTest(head=head):
                args = json.dumps({"command": f"{head} something"})
                self.assertEqual(classify("Bash", args), intent)

    def test_shell_family_aliases_all_parse_commands(self):
        for name in ("exec_command", "shell_command", "bash", "shell"):
            self.assertEqual(classify(name, '{"command": "ls -la"}'), "explore")

    def test_exec_is_repl_not_shell(self):
        # codex's exec is a JS REPL — arguments are raw code, never parsed
        self.assertEqual(
            classify("exec", '{"arguments": "const names=ALL_TOOLS.filter(t=>t)"}'),
            "repl",
        )
        self.assertEqual(classify("exec", '{"arguments": "console.log(1)"}'), "repl")

    def test_exec_command_still_shell_parses(self):
        self.assertEqual(classify("exec_command", '{"cmd": "ls"}'), "explore")

    def test_new_command_heads_from_corpus(self):
        for head, intent in [
            ("cd", "explore"), ("pwd", "explore"), ("env", "explore"),
            ("which", "explore"), ("whoami", "explore"), ("uname", "explore"),
            ("date", "explore"), ("curl", "explore"), ("awk", "explore"),
            ("basename", "explore"),
            ("rm", "mutate"), ("mkdir", "mutate"), ("cp", "mutate"),
            ("mv", "mutate"), ("touch", "mutate"), ("chmod", "mutate"),
            ("export", "meta"), ("unset", "meta"), ("set", "meta"),
            ("source", "meta"), ("alias", "meta"),
            ("uv", "test-build"), ("pip", "test-build"), ("brew", "test-build"),
            ("swift", "test-build"), ("ruff", "test-build"),
            ("eslint", "test-build"), ("prettier", "test-build"),
        ]:
            with self.subTest(head=head):
                args = json.dumps({"command": f"{head} something"})
                self.assertEqual(classify("Bash", args), intent)

    def test_ticktick_prefix(self):
        full = "mcp__plugin-learning-depot_ticktick__list_projects"
        self.assertEqual(classify(full, ""), "explore")
        # hash-suffixed truncated variant
        self.assertEqual(
            classify("mcp__plugin-learning-depot_ticktick__ab12cd", ""), "explore"
        )

    def test_context7_prefix(self):
        self.assertEqual(
            classify("mcp__codex_apps__context7_resolve_library_id", ""), "explore"
        )
        self.assertEqual(
            classify("mcp__codex_apps__context7_query_docs", ""), "explore"
        )

    def test_fetchurl(self):
        self.assertEqual(classify("FetchURL", ""), "explore")

    def test_flags_and_absolute_paths(self):
        self.assertEqual(
            classify("Bash", '{"command": "rg -n pattern src/"}'), "explore"
        )
        self.assertEqual(
            classify("Bash", '{"command": "/usr/bin/rg pattern"}'), "explore"
        )

    def test_cmd_field_alias(self):
        self.assertEqual(classify("exec_command", '{"cmd": "grep x f"}'), "explore")

    def test_unknown_and_malformed_all_go_to_other(self):
        self.assertEqual(
            classify("Bash", '{"command": "zig build"}'), "other"
        )  # unknown head
        self.assertEqual(classify("Bash", "not json at all"), "other")
        self.assertEqual(classify("Bash", '["cat", "foo"]'), "other")  # not a dict
        self.assertEqual(classify("Bash", "{}"), "other")  # no command field
        self.assertEqual(classify("Bash", '{"command": ""}'), "other")
        self.assertEqual(classify("Bash", '{"command": 42}'), "other")
        self.assertEqual(classify("mystery_tool", ""), "other")
        self.assertEqual(classify("", ""), "other")

    def test_non_string_inputs(self):
        self.assertEqual(classify(123, ""), "other")
        self.assertEqual(classify("Bash", None), "other")

    def test_unterminated_quote(self):
        self.assertEqual(
            classify("Bash", '{"command": "cat \\"unterminated"}'), "other"
        )

    def test_comment_only_command(self):
        self.assertEqual(
            classify("Bash", '{"command": "# just a comment"}'), "other"
        )


if __name__ == "__main__":
    unittest.main()
