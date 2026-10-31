#!/usr/bin/env python3
"""Unit tests for dispatch_form.py, check_dispatch.py, orchestrator_guard.py and hook_state.py.

The two hooks are also driven end to end, as Claude Code drives them: a JSON
payload on stdin, the verdict in the exit code, the reason on stderr.
"""

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import check_dispatch  # noqa: E402
import dispatch_form  # noqa: E402
import hook_state  # noqa: E402
import orchestrator_guard  # noqa: E402

FORM = (
    "Repository: aunger/gallery-button-for-pixel-camera\n"
    "Issue: #164\n"
    "PR: None\n"
    "Branch: feature/issue-164-orchestrate-plugin\n"
    "Verification plan comment: None\n"
)


def agent_call(subagent_type, prompt, **extra):
    tool_input = {"subagent_type": subagent_type, "prompt": prompt, "description": "dispatch"}
    tool_input.update(extra)
    return {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": tool_input}


def run_hook(script, payload):
    """Run a hook script as Claude Code would; return (exit code, stderr)."""
    completed = subprocess.run(
        [sys.executable, os.path.join(HERE, script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stderr


class TestFormErrors(unittest.TestCase):
    def test_the_form_passes(self):
        self.assertEqual(dispatch_form.form_errors(FORM), [])

    def test_surrounding_blank_lines_and_trailing_spaces_are_tolerated(self):
        padded = "\n\n" + FORM.replace("\n", "   \n") + "\n\n"
        self.assertEqual(dispatch_form.form_errors(padded), [])

    def test_every_none_value_passes(self):
        text = (
            "Repository: o/r\nIssue: None\nPR: None\nBranch: None\n"
            "Verification plan comment: None\n"
        )
        self.assertEqual(dispatch_form.form_errors(text), [])

    def test_numbers_pass(self):
        text = "Repository: o/r\nIssue: #1\nPR: #22\nBranch: fix/issue-1-x\nVerification plan comment: 3344\n"
        self.assertEqual(dispatch_form.form_errors(text), [])

    def test_a_sixth_line_is_an_error(self):
        errors = dispatch_form.form_errors(FORM + "Please focus on the tests.\n")
        self.assertTrue(any("line 6 is not part of the form" in e for e in errors), errors)

    def test_a_preamble_is_an_error(self):
        errors = dispatch_form.form_errors("You are a Programmer.\n" + FORM)
        self.assertTrue(any("line 1 must begin with 'Repository: '" in e for e in errors), errors)

    def test_a_missing_line_is_an_error(self):
        errors = dispatch_form.form_errors("\n".join(FORM.splitlines()[:4]))
        self.assertTrue(any("line 5 is missing" in e for e in errors), errors)

    def test_order_matters(self):
        lines = FORM.splitlines()
        swapped = "\n".join([lines[1], lines[0]] + lines[2:])
        self.assertNotEqual(dispatch_form.form_errors(swapped), [])

    def test_a_bare_number_is_not_an_issue(self):
        errors = dispatch_form.form_errors(FORM.replace("Issue: #164", "Issue: 164"))
        self.assertTrue(any("not a valid Issue value" in e for e in errors), errors)

    def test_a_comment_id_is_bare_digits_not_a_url(self):
        bad = FORM.replace(
            "Verification plan comment: None",
            "Verification plan comment: https://github.com/o/r/pull/1#issuecomment-5",
        )
        self.assertNotEqual(dispatch_form.form_errors(bad), [])

    def test_a_repository_needs_owner_and_name(self):
        self.assertNotEqual(dispatch_form.form_errors(FORM.replace("aunger/", "")), [])

    def test_a_branch_with_a_space_is_an_error(self):
        self.assertNotEqual(
            dispatch_form.form_errors(FORM.replace("Branch: feature", "Branch: my feature")), []
        )

    def test_non_text_is_an_error(self):
        self.assertEqual(dispatch_form.form_errors(None), ["the prompt is not text"])

    def test_the_refusal_names_the_errors_and_shows_the_form(self):
        message = dispatch_form.refusal("the prompt", ["line 6 is not part of the form: 'x'"])
        self.assertIn("refused: the prompt is not the dispatch form", message)
        self.assertIn("- line 6 is not part of the form", message)
        self.assertIn("Verification plan comment: {numeric comment id} or None", message)


class TestCheckDispatch(unittest.TestCase):
    def test_plugin_agent_with_the_form_passes(self):
        for name in dispatch_form.AGENT_NAMES:
            code, _ = check_dispatch.decide(agent_call("orchestrate:" + name, FORM))
            self.assertEqual(code, 0, name)

    def test_plugin_agent_with_prose_is_refused(self):
        code, message = check_dispatch.decide(
            agent_call("orchestrate:reviewer", "Review PR #12 carefully.\n" + FORM)
        )
        self.assertEqual(code, 2)
        self.assertIn("orchestrate:reviewer dispatch", message)

    def test_other_agents_pass_through(self):
        for subagent_type in ("general-purpose", "Explore", "deep-planner", "other:reviewer"):
            code, message = check_dispatch.decide(agent_call(subagent_type, "Do anything."))
            self.assertEqual((code, message), (0, ""), subagent_type)

    def test_other_tools_pass_through(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        self.assertEqual(check_dispatch.decide(payload), (0, ""))

    def test_malformed_payloads_pass_through(self):
        for payload in ({}, [], {"tool_name": "Agent"}, {"tool_name": "Agent", "tool_input": "x"}):
            self.assertEqual(check_dispatch.decide(payload), (0, ""), payload)

    def test_end_to_end_refusal(self):
        code, stderr = run_hook(
            "check_dispatch.py", agent_call("orchestrate:programmer", "Fix the bug in Foo.kt.")
        )
        self.assertEqual(code, 2)
        self.assertIn("[orchestrate] refused", stderr)

    def test_end_to_end_pass(self):
        code, stderr = run_hook("check_dispatch.py", agent_call("orchestrate:programmer", FORM))
        self.assertEqual((code, stderr), (0, ""))

    def test_unreadable_input_never_blocks(self):
        completed = subprocess.run(
            [sys.executable, os.path.join(HERE, "check_dispatch.py")],
            input="not json",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("could not read its input", completed.stderr)


def main_call(tool_name, **tool_input):
    return {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input}


class TestOrchestratorGuard(unittest.TestCase):
    def test_edits_are_refused_in_the_main_session(self):
        for tool in ("Edit", "Write", "NotebookEdit"):
            code, message = orchestrator_guard.decide(main_call(tool, file_path="app/Foo.kt"))
            self.assertEqual(code, 2, tool)
            self.assertIn("does not edit or write files", message)

    def test_a_sub_agent_may_edit(self):
        payload = main_call("Edit", file_path="app/Foo.kt")
        payload["agent_id"] = "agent-123"
        self.assertEqual(orchestrator_guard.decide(payload), (0, ""))

    def test_reading_markdown_and_dot_claude_files_is_allowed(self):
        for path in (
            "AGENTS.md",
            "/repo/agents/code_edit.md",
            "/repo/.claude/orchestrate.json",
            "/repo/.claude/skills/orchestrate/reference/ci-monitor-loop.md",
            "C:\\repo\\.claude\\settings.json",
        ):
            self.assertEqual(
                orchestrator_guard.decide(main_call("Read", file_path=path)), (0, ""), path
            )

    def test_reading_source_is_refused(self):
        for path in ("app/src/main/java/Foo.kt", "scripts/ci/x.py", "build.gradle.kts", ""):
            code, message = orchestrator_guard.decide(main_call("Read", file_path=path))
            self.assertEqual(code, 2, path)
            self.assertIn("does not read source files", message)

    def test_send_message_must_be_the_form(self):
        self.assertEqual(
            orchestrator_guard.decide(main_call("SendMessage", to="agent-1", message=FORM)), (0, "")
        )
        code, message = orchestrator_guard.decide(
            main_call("SendMessage", to="agent-1", message="Please also fix the tests.")
        )
        self.assertEqual(code, 2)
        self.assertIn("this SendMessage is not the dispatch form", message)

    def test_other_tools_pass_through(self):
        self.assertEqual(orchestrator_guard.decide(main_call("Bash", command="date -u")), (0, ""))
        self.assertEqual(orchestrator_guard.decide({}), (0, ""))

    def test_end_to_end(self):
        code, stderr = run_hook("orchestrator_guard.py", main_call("Write", file_path="x.py"))
        self.assertEqual(code, 2)
        self.assertIn("Orchestrator mode", stderr)
        code, stderr = run_hook("orchestrator_guard.py", main_call("Read", file_path="README.md"))
        self.assertEqual((code, stderr), (0, ""))


if __name__ == "__main__":
    unittest.main()


class TestHookState(unittest.TestCase):
    """hook_state.py: the switch both hooks consult first."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, ".state", "disengaged-sessions")

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_state_is_engaged(self):
        self.assertFalse(hook_state.hooks_disengaged("s1", self.state, environ={}))
        self.assertFalse(hook_state.hooks_disengaged(None, self.state, environ={}))

    def test_disengage_then_reengage_one_session(self):
        hook_state.disengage("s1", self.state)
        self.assertTrue(hook_state.hooks_disengaged("s1", self.state, environ={}))
        self.assertFalse(hook_state.hooks_disengaged("s2", self.state, environ={}))
        hook_state.disengage("s1", self.state)
        self.assertEqual(hook_state.listed_sessions(self.state), ["s1"])
        hook_state.reengage("s1", self.state)
        self.assertFalse(hook_state.hooks_disengaged("s1", self.state, environ={}))
        self.assertEqual(hook_state.listed_sessions(self.state), [])

    def test_environment_variable_disengages_every_session(self):
        for value in ("1", "yes", "disengaged"):
            self.assertTrue(
                hook_state.hooks_disengaged("s1", self.state, {hook_state.ENV_VAR: value}), value
            )
            self.assertTrue(
                hook_state.hooks_disengaged(None, self.state, {hook_state.ENV_VAR: value}), value
            )
        for value in ("", "0", "false", "False"):
            self.assertFalse(
                hook_state.hooks_disengaged("s1", self.state, {hook_state.ENV_VAR: value}), value
            )

    def test_status_lines(self):
        self.assertEqual(
            hook_state.status("s1", self.state, environ={}),
            "orchestrate hooks engaged for session s1",
        )
        hook_state.disengage("s1", self.state)
        self.assertEqual(
            hook_state.status("s1", self.state, environ={}),
            "orchestrate hooks disengaged for session s1",
        )
        self.assertIn(
            hook_state.ENV_VAR, hook_state.status("s1", self.state, {hook_state.ENV_VAR: "1"})
        )

    def test_cli(self):
        def cli(*args):
            return subprocess.run(
                [
                    sys.executable,
                    os.path.join(HERE, "hook_state.py"),
                    *args,
                    "--state-file",
                    self.state,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(
            cli("disengage", "--session", "abc").stdout.strip(),
            "orchestrate hooks disengaged for session abc",
        )
        self.assertEqual(
            cli("status", "--session", "abc").stdout.strip(),
            "orchestrate hooks disengaged for session abc",
        )
        self.assertEqual(
            cli("reengage", "--session", "abc").stdout.strip(),
            "orchestrate hooks engaged for session abc",
        )
        missing = cli("disengage")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("needs --session", missing.stderr)

    def test_a_disengaged_session_passes_both_hooks(self):
        hook_state.disengage("s1", self.state)
        with (
            patch.object(hook_state, "STATE_FILE", self.state),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop(hook_state.ENV_VAR, None)
            bad_dispatch = agent_call("orchestrate:programmer", "Fix the bug.")
            bad_dispatch["session_id"] = "s1"
            self.assertEqual(check_dispatch.decide(bad_dispatch), (0, ""))
            bad_dispatch["session_id"] = "s2"
            self.assertEqual(check_dispatch.decide(bad_dispatch)[0], 2)
            edit = main_call("Edit", file_path="app/Foo.kt")
            edit["session_id"] = "s1"
            self.assertEqual(orchestrator_guard.decide(edit), (0, ""))
            edit["session_id"] = "s2"
            self.assertEqual(orchestrator_guard.decide(edit)[0], 2)

    def test_the_hooks_read_the_default_state_file_of_the_plugin(self):
        # Both hooks call hooks_disengaged() with the module default, which is
        # .state/disengaged-sessions under the plugin root, where the skill writes.
        plugin_root = os.path.dirname(os.path.dirname(HERE))
        self.assertEqual(
            hook_state.STATE_FILE, os.path.join(plugin_root, ".state", "disengaged-sessions")
        )
