#!/usr/bin/env python3
"""Unit tests for the orchestrate plugin's hook scripts in this directory.

The two hooks are also driven end to end, as Claude Code drives them: a JSON
payload on stdin, the verdict in the exit code, the reason on stderr.
"""

import io
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
import protect_state  # noqa: E402

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

    def test_disengage_then_engage_one_session(self):
        hook_state.disengage("s1", self.state)
        self.assertTrue(hook_state.hooks_disengaged("s1", self.state, environ={}))
        self.assertFalse(hook_state.hooks_disengaged("s2", self.state, environ={}))
        hook_state.disengage("s1", self.state)
        self.assertEqual(hook_state.listed_sessions(self.state), ["s1"])
        hook_state.engage("s1", self.state)
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

    def expansion(self, command_args, command_name="orchestrate:orch-hooks", session_id="s1"):
        payload = {
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "command_name": command_name,
            "command_args": command_args,
            "command_source": "plugin",
            "session_id": session_id,
        }
        return hook_state.expansion_output(payload, self.state, environ={})

    def context_of(self, output):
        return output["hookSpecificOutput"]["additionalContext"]

    def test_the_expansion_hook_applies_each_command(self):
        self.assertEqual(
            self.context_of(self.expansion("disengage")),
            "orch-hooks result: orchestrate hooks disengaged for session s1",
        )
        self.assertTrue(hook_state.hooks_disengaged("s1", self.state, environ={}))
        self.assertEqual(
            self.context_of(self.expansion("status")),
            "orch-hooks result: orchestrate hooks disengaged for session s1",
        )
        self.assertEqual(
            self.context_of(self.expansion(" engage ")),
            "orch-hooks result: orchestrate hooks engaged for session s1",
        )
        self.assertFalse(hook_state.hooks_disengaged("s1", self.state, environ={}))

    def test_the_bare_command_name_counts_too(self):
        self.assertIn("disengaged", self.context_of(self.expansion("disengage", "orch-hooks")))

    def test_other_commands_are_left_alone(self):
        for name in ("orchestrate:orchestrate", "orchestrate", "other:orch-hooks-x", "", None):
            self.assertIsNone(self.expansion("disengage", name), name)
        self.assertEqual(hook_state.listed_sessions(self.state), [])

    def test_bad_arguments_block_the_expansion_and_change_nothing(self):
        for args in ("", "off", "disengage now", None):
            output = self.expansion(args)
            self.assertEqual(output["decision"], "block", args)
            self.assertIn("Usage: /orch-hooks disengage | engage | status", output["reason"])
        self.assertEqual(hook_state.listed_sessions(self.state), [])

    def test_no_session_id_blocks(self):
        self.assertEqual(self.expansion("disengage", session_id="")["decision"], "block")
        self.assertEqual(hook_state.listed_sessions(self.state), [])

    def test_the_script_has_no_command_line_that_changes_state(self):
        completed = subprocess.run(
            [sys.executable, os.path.join(HERE, "hook_state.py"), "disengage", "--session", "x"],
            input="",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("could not read its input", completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_the_hook_end_to_end_prints_json(self):
        payload = {
            "hook_event_name": "UserPromptExpansion",
            "command_name": "orchestrate:orch-hooks",
            "command_args": "status",
            "session_id": "end-to-end-session-that-is-never-listed",
        }
        completed = subprocess.run(
            [sys.executable, os.path.join(HERE, "hook_state.py")],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        context = json.loads(completed.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith("orch-hooks result: orchestrate hooks "), context)

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


class TestOrchestratorGuardCoverage(unittest.TestCase):
    """The worktree, Grep, Bash and Monitor rules of orchestrator_guard.py."""

    ROOT = orchestrator_guard.PLUGIN_ROOT

    def decide(self, tool, **tool_input):
        return orchestrator_guard.decide(main_call(tool, **tool_input))

    def test_nothing_under_a_worktree_may_be_read(self):
        for path in (
            "/repo/.claude/worktrees/agent-x/app/src/main/java/Foo.kt",
            "/repo/.claude/worktrees/agent-x/README.md",
            "/repo/.claude/worktrees/agent-x/.claude/orchestrate.json",
            "C:\\repo\\.claude\\worktrees\\agent-x\\Foo.kt",
        ):
            code, message = self.decide("Read", file_path=path)
            self.assertEqual(code, 2, path)
            self.assertIn(".claude/worktrees/", message)

    def test_a_directory_merely_named_worktrees_is_not_special(self):
        self.assertEqual(self.decide("Read", file_path="/repo/docs/worktrees/notes.md"), (0, ""))

    def test_grep(self):
        self.assertEqual(self.decide("Grep", pattern="x", path="/repo/AGENTS.md"), (0, ""))
        self.assertEqual(self.decide("Grep", pattern="x", path="/repo/.claude/skills"), (0, ""))
        self.assertEqual(self.decide("Grep", pattern="x", glob="**/*.md"), (0, ""))
        for tool_input in (
            {"pattern": "x"},
            {"pattern": "x", "path": "/repo/app/src"},
            {"pattern": "x", "path": "/repo/.claude/worktrees/agent-x"},
            {"pattern": "x", "path": "/repo/.claude/worktrees/agent-x", "glob": "*.md"},
            {"pattern": "x", "glob": "*.kt"},
        ):
            self.assertEqual(self.decide("Grep", **tool_input)[0], 2, tool_input)

    def test_allowed_commands(self):
        for command in (
            "date -u",
            "date",
            "date -u +%Y-%m-%dT%H:%M:%SZ",
            "sleep 300",
            "git branch feature/issue-7-x origin/main",
            "git branch",
            "git fetch origin main",
            "git status --short",
            "git rev-parse HEAD",
            "git ls-remote origin",
            self.ROOT
            + "/scripts/update_gh_labels.sh o r 7 --remove orchestrate --add orchestrating",
            self.ROOT + '/scripts/update_gh_labels.sh o r 7 --add "changes requested"',
            "bash " + self.ROOT + "/scripts/update_gh_labels.sh o r 7 --add verified",
            "python3 "
            + self.ROOT
            + "/scripts/ci_monitor/ci_monitor.py --pr 7 --repo o/r --config /p/.claude/orchestrate.json",
            "python3 " + self.ROOT + "/scripts/check_config.py /p/.claude/orchestrate.json",
            "python3 " + self.ROOT + "/scripts/dispatch_cost.py --json",
        ):
            self.assertTrue(orchestrator_guard.command_allowed(command), command)
            self.assertEqual(self.decide("Bash", command=command), (0, ""), command)

    def test_refused_commands(self):
        for command in (
            "cat app/src/main/java/Foo.kt",
            "grep -r TODO app",
            "sed -i s/a/b/ Foo.kt",
            "python3 -c 'print(1)'",
            "date -s 2020-01-01",
            "sleep 1m",
            "git log -p",
            "git diff origin/main",
            "git show HEAD",
            "git checkout main",
            "git branch -D feature/x",
            "git branch --move a b",
            "git -C /elsewhere status",
            "date -u; cat Foo.kt",
            "date -u && cat Foo.kt",
            "date -u | tee x",
            "date -u > x",
            "echo $(cat Foo.kt)",
            "sleep 300 &",
            "python3 " + self.ROOT + "/scripts/hooks/hook_state.py disengage --session s",
            "python3 " + self.ROOT + "/scripts/link_gh_issues.py add o r 1 --blocked-by 2",
            "python3 " + self.ROOT + "/scripts/../../../../app/x.py",
            "python3 /elsewhere/scripts/ci_monitor/ci_monitor.py --pr 7",
            "python3",
            "'unterminated",
            "",
        ):
            self.assertFalse(orchestrator_guard.command_allowed(command), command)
            code, message = self.decide("Bash", command=command)
            self.assertEqual(code, 2, command)
            self.assertIn("runs only these commands", message)

    def test_a_relative_plugin_script_path_resolves_against_cwd(self):
        project = os.path.dirname(os.path.dirname(self.ROOT))
        relative = os.path.relpath(self.ROOT, project) + "/scripts/check_config.py x.json"
        self.assertTrue(orchestrator_guard.command_allowed("python3 " + relative, cwd=project))
        self.assertFalse(orchestrator_guard.command_allowed("python3 " + relative, cwd="/"))

    def test_monitor_follows_the_bash_rules(self):
        ok = "python3 " + self.ROOT + "/scripts/ci_monitor/ci_monitor.py --pr 7"
        self.assertEqual(self.decide("Monitor", command=ok), (0, ""))
        self.assertEqual(self.decide("Monitor", command="tail -F app.log")[0], 2)
        self.assertEqual(self.decide("Monitor", url="wss://example.invalid"), (0, ""))

    def test_a_sub_agent_may_run_anything(self):
        payload = main_call("Bash", command="cat app/src/main/java/Foo.kt")
        payload["agent_id"] = "agent-1"
        self.assertEqual(orchestrator_guard.decide(payload), (0, ""))


class TestProtectState(unittest.TestCase):
    def test_calls_naming_the_state_file_are_refused(self):
        for tool, tool_input in (
            (
                "Bash",
                {"command": "echo s1 >> .claude/skills/orchestrate/.state/disengaged-sessions"},
            ),
            ("Bash", {"command": "rm /p/.state/disengaged-sessions"}),
            ("Write", {"file_path": "/p/.state/disengaged-sessions", "content": "s1"}),
            ("Edit", {"file_path": "/p/.state/disengaged-sessions"}),
            ("NotebookEdit", {"notebook_path": "/p/.state/disengaged-sessions"}),
            ("Monitor", {"command": "tail -F /p/.state/disengaged-sessions"}),
        ):
            payload = {"tool_name": tool, "tool_input": tool_input}
            code, message = protect_state.decide(payload)
            self.assertEqual(code, 2, tool_input)
            self.assertIn("/orch-hooks", message)
            payload["agent_id"] = "agent-1"
            self.assertEqual(protect_state.decide(payload)[0], 2, "sub-agents too")

    def test_other_calls_pass(self):
        for payload in (
            {"tool_name": "Bash", "tool_input": {"command": "date -u"}},
            {"tool_name": "Write", "tool_input": {"file_path": "/p/notes.md"}},
            {"tool_name": "Bash", "tool_input": "not a dict"},
            {},
            [],
        ):
            self.assertEqual(protect_state.decide(payload), (0, ""), payload)

    def test_end_to_end(self):
        code, stderr = run_hook(
            "protect_state.py",
            {"tool_name": "Bash", "tool_input": {"command": "cat x/.state/disengaged-sessions"}},
        )
        self.assertEqual(code, 2)
        self.assertIn("refused", stderr)


class TestFaultsNeverBlock(unittest.TestCase):
    """A payload of an unexpected shape passes, and a crash exits 0 with a warning."""

    def test_odd_shapes_pass(self):
        for payload in (
            {"tool_name": "Agent", "tool_input": {"subagent_type": ["orchestrate:reviewer"]}},
            {"tool_name": "Agent", "tool_input": {"subagent_type": {"a": 1}, "prompt": "x"}},
            {"tool_name": ["Edit"], "tool_input": {}},
            {"tool_name": {"x": 1}},
        ):
            self.assertEqual(check_dispatch.decide(payload), (0, ""), payload)
            self.assertEqual(orchestrator_guard.decide(payload), (0, ""), payload)

    def test_a_crash_exits_zero_with_a_warning(self):
        for module, script in (
            (check_dispatch, "check_dispatch.py"),
            (orchestrator_guard, "orchestrator_guard.py"),
        ):
            with (
                patch.object(module, "decide", side_effect=RuntimeError("boom")),
                patch("sys.stdin", new=io.StringIO("{}")),
                patch("sys.stderr", new=io.StringIO()) as err,
            ):
                self.assertEqual(module.main(), 0, script)
            self.assertIn(script + " failed: RuntimeError('boom')", err.getvalue())

    def test_an_unhashable_type_end_to_end(self):
        code, stderr = run_hook(
            "check_dispatch.py",
            {"tool_name": "Agent", "tool_input": {"subagent_type": ["x"], "prompt": "y"}},
        )
        self.assertEqual((code, stderr), (0, ""))
