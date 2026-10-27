"""Integration and parser contracts; Git runs only in temporary repositories."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import git_scope_gate as gate_module

HERE = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("git"), "Git is unavailable")
class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="git-scope-gate-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Scope Test")
        self.git("config", "user.email", "scope@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.policy = self.base / "policy.json"
        self.policy.write_text('{"allow":["src/"]}', encoding="utf-8")

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(["git", "-C", str(self.repo), *args], env=env,
                              capture_output=True, check=True).stdout

    def put(self, path, value="hello\n"):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8", newline="\n")

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "-qm", "fixture")

    def gate(self, expected, *, repo=None, env=None):
        p = subprocess.run([sys.executable, str(HERE / "git_scope_gate.py"),
                            "--repo", str(repo or self.repo), "--policy", str(self.policy),
                            "--format", "json"], capture_output=True, env=env)
        self.assertEqual(p.returncode, expected, p.stderr.decode(errors="replace") + p.stdout.decode(errors="replace"))
        result = json.loads(p.stdout)
        self.assertEqual(result["exit_code"], expected)
        return result

    def test_unborn_empty_is_clean(self):
        result = self.gate(0)
        self.assertEqual(result["records"], [])

    def test_staged_unstaged_untracked_and_delete(self):
        self.put("src/staged")
        self.put("src/unstaged")
        self.put("src/deleted")
        self.commit()
        self.put("src/staged", "changed")
        self.git("add", "src/staged")
        self.put("src/unstaged", "changed")
        self.put("src/untracked")
        (self.repo / "src/deleted").unlink()
        result = self.gate(0)
        self.assertEqual({r["status"] for r in result["records"]}, {"M ", " M", " D", "??"})

    def test_rename_both_endpoints_checked(self):
        self.put("outside")
        self.commit()
        self.git("mv", "outside", "src-target")
        self.policy.write_text('{"allow":["src-target"]}', encoding="utf-8")
        result = self.gate(1)
        self.assertEqual(result["records"][0]["paths"], ["src-target", "outside"])
        self.assertEqual(result["violations"][0]["path"], "outside")

    def test_scope_boundary_deny_case(self):
        self.put("src/allowed")
        self.put("src/private/key")
        self.put("src-other")
        self.policy.write_text('{"allow":["src/"],"deny":["src/private/"]}', encoding="utf-8")
        result = self.gate(1)
        self.assertEqual({v["path"] for v in result["violations"]}, {"src/private/key", "src-other"})

    def test_policy_case_is_sensitive(self):
        self.put("src/case")
        self.policy.write_text('{"allow":["Src/"]}', encoding="utf-8")
        self.assertEqual(self.gate(1)["violations"][0]["path"], "src/case")

    def test_subdirectory_rejected(self):
        (self.repo / "src").mkdir()
        self.gate(2, repo=self.repo / "src")

    def test_invalid_policies(self):
        for policy in ['{}', '{"allow":[]}', '{"allow":["../x"]}', '{"allow":["/x"]}',
                       '{"allow":["C:x"]}', '{"allow":["x\\\\y"]}', '{"allow":["x*"]}',
                       '{"allow":["x"],"extra":[]}', '{"allow":[1]}',
                       '{"allow":["x"],"allow":["y"]}', '{"allow":["a//b"]}',
                       '{"allow":["a/./b"]}', '{"allow":["a\\n"]}',
                       '{"allow":["a\\u0085"]}']:
            with self.subTest(policy=policy):
                self.policy.write_text(policy, encoding="utf-8")
                self.gate(2)
        self.policy.write_bytes(b'\xff')
        self.gate(2)

    def test_spaces_are_unambiguous(self):
        self.put("src/a name")
        self.assertEqual(self.gate(0)["records"][0]["paths"], ["src/a name"])

    @unittest.skipIf(os.name == "nt", "Windows forbids tab and newline filename characters")
    def test_tab_and_newline_names(self):
        self.put("src/a\tb\nc")
        self.assertEqual(self.gate(0)["records"][0]["paths"], ["src/a\tb\nc"])

    def test_polluted_git_environment_cannot_redirect(self):
        self.put("outside")
        env = dict(os.environ)
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE"):
            env[key] = str(self.base / "nonexistent")
        self.gate(1, env=env)

    def test_merge_conflict_rejected(self):
        self.put("src/conflict", "base")
        self.commit()
        branch = self.git("branch", "--show-current").decode().strip()
        self.git("checkout", "-qb", "other")
        self.put("src/conflict", "other")
        self.commit()
        self.git("checkout", "-q", branch)
        self.put("src/conflict", "main")
        self.commit()
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("merge", "other")
        self.gate(2)

    def test_nested_untracked_repo_rejected(self):
        nested = self.repo / "src/nested"
        nested.mkdir(parents=True)
        subprocess.run(["git", "-C", str(nested), "init", "-q"], check=True, capture_output=True)
        (nested / "file").write_text("nested", encoding="utf-8")
        self.gate(2)

    def test_dirty_gitlink_rejected(self):
        self.put("src/base")
        self.commit()
        oid = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "160000," + oid + ",src/sub")
        self.gate(2)

    def test_ignored_outside_file_is_out_of_inventory(self):
        self.put(".gitignore", "ignored\n")
        self.commit()
        self.put("ignored")
        self.gate(0)

    def test_staged_gitlink_deletion_rejected(self):
        self.put("src/base")
        self.commit()
        oid = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "160000," + oid + ",src/sub")
        self.git("commit", "-qm", "add gitlink")
        self.git("update-index", "--force-remove", "src/sub")
        self.gate(2)

    def test_actual_dirty_submodule_rejected(self):
        child = self.base / "child"
        child.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        def child_git(*args):
            subprocess.run(["git", "-C", str(child), *args], env=env, check=True, capture_output=True)
        child_git("init", "-q")
        child_git("config", "user.name", "Scope Test")
        child_git("config", "user.email", "scope@example.invalid")
        (child / "file").write_text("base", encoding="utf-8")
        child_git("add", "file")
        child_git("commit", "-qm", "base")
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "--quiet", str(child), "src/sub")
        self.commit()
        (self.repo / "src/sub/file").write_text("changed", encoding="utf-8")
        self.gate(2)

    def test_unstaged_move_is_separate_endpoints(self):
        self.put("outside")
        self.commit()
        (self.repo / "src").mkdir()
        (self.repo / "outside").rename(self.repo / "src/moved")
        result = self.gate(1)
        self.assertEqual({r["status"] for r in result["records"]}, {" D", "??"})

    def test_crossing_rename_out_of_scope(self):
        self.put("src/origin")
        self.commit()
        self.git("mv", "src/origin", "outside")
        result = self.gate(1)
        self.assertEqual(result["records"][0]["paths"], ["outside", "src/origin"])

    def test_closed_stdout_returns_two(self):
        for output_format in ("json", "text"):
            with self.subTest(output_format=output_format):
                p = subprocess.Popen([sys.executable, str(HERE / "git_scope_gate.py"),
                                      "--repo", str(self.repo), "--policy", str(self.policy),
                                      "--format", output_format], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                p.stdout.close()
                self.assertEqual(p.wait(timeout=10), 2)
                self.assertEqual(p.stderr.read(), b"")
                p.stderr.close()

    def test_configured_fsmonitor_not_executed(self):
        sentinel = self.base / "hook-ran"
        hook = self.base / "monitor.sh"
        hook.write_text('#!/bin/sh\nprintf hook > "' + sentinel.as_posix() + '"\n', encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        self.git("config", "core.fsmonitor", hook.as_posix())
        env = dict(os.environ)
        env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
                    "GIT_CONFIG_VALUE_0": hook.as_posix(),
                    "GIT_CONFIG_PARAMETERS": "'core.fsmonitor='" + hook.as_posix()})
        self.gate(0, env=env)
        self.assertFalse(sentinel.exists())

    def test_inspection_does_not_write_index_or_config(self):
        self.put("src/base")
        self.commit()
        self.put("src/base", "changed contents")
        files = [self.repo / ".git/index", self.repo / ".git/config"]
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in files]
        self.gate(0)
        after = [(p.read_bytes(), p.stat().st_mtime_ns) for p in files]
        self.assertEqual(before, after)

    def filter_guard_case(self, kind):
        marker = self.base / "filter-ran"
        helper = self.base / "filter.py"
        helper.write_text("import pathlib,sys\npathlib.Path(" + repr(str(marker)) +
                          ").write_text('invoked')\n" +
                          ("sys.stdout.buffer.write(sys.stdin.buffer.read())\n" if kind == "clean" else "raise SystemExit(1)\n"),
                          encoding="utf-8", newline="\n")
        self.put(".gitattributes", "*.txt filter=probe\n")
        self.put("src/file.txt", "aaaa\n")
        self.commit()
        command = '"' + sys.executable.replace("\\", "/") + '" "' + helper.as_posix() + '"'
        self.git("config", "filter.probe." + kind, command)
        self.put("src/file.txt", "bbbb\n")
        p = subprocess.run([sys.executable, str(HERE / "git_scope_gate.py"), "--repo", str(self.repo),
                            "--policy", str(self.policy), "--format", "json"], capture_output=True, timeout=40)
        self.assertFalse(marker.exists(), "Git executed the configured " + kind + " filter")
        self.assertEqual(p.returncode, 2, p.stdout.decode(errors="replace"))

    def test_clean_filter_is_rejected_without_execution(self):
        self.filter_guard_case("clean")

    def test_process_filter_is_rejected_without_execution(self):
        self.filter_guard_case("process")

    def test_unused_configured_filters_are_allowed(self):
        self.put("src/ordinary.bin")
        self.put(".gitattributes", "*.txt filter=unused\n")
        self.commit()
        self.git("config", "filter.unused.clean", "must-never-run")
        self.git("config", "filter.unused.process", "must-never-run")
        self.put("src/ordinary.bin", "changed")
        self.gate(0)

    def test_later_empty_filter_configuration_overrides_command(self):
        self.put(".gitattributes", "*.txt filter=probe\n")
        self.put("src/ordinary.txt")
        self.commit()
        self.git("config", "--add", "filter.probe.clean", "must-never-run")
        self.git("config", "--add", "filter.probe.clean", "")
        self.put("src/ordinary.txt", "changed")
        self.gate(0)

    def test_clean_gitlink_is_rejected_before_status(self):
        self.put("src/base")
        self.commit()
        oid = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "160000," + oid + ",src/sub")
        self.git("commit", "-qm", "add gitlink")
        self.gate(2)

    def test_submodule_filter_is_not_executed(self):
        child = self.base / "child"
        child.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        def child_git(*args):
            subprocess.run(["git", "-C", str(child), *args], env=env, check=True, capture_output=True)
        child_git("init", "-q")
        child_git("config", "user.name", "Scope Test")
        child_git("config", "user.email", "scope@example.invalid")
        (child / ".gitattributes").write_text("*.txt filter=probe\n", encoding="utf-8")
        (child / "file.txt").write_text("aaaa\n", encoding="utf-8")
        child_git("add", "--all")
        child_git("commit", "-qm", "base")
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "--quiet", str(child), "src/sub")
        self.commit()
        marker = self.base / "submodule-filter-ran"
        helper = self.base / "submodule_filter.py"
        helper.write_text("import pathlib,sys\npathlib.Path(" + repr(str(marker)) +
                          ").write_text('invoked')\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n",
                          encoding="utf-8", newline="\n")
        command = '"' + sys.executable.replace("\\", "/") + '" "' + helper.as_posix() + '"'
        self.git("-C", str(self.repo / "src/sub"), "config", "filter.probe.clean", command)
        (self.repo / "src/sub/file.txt").write_text("bbbb\n", encoding="utf-8")
        self.gate(2)
        self.assertFalse(marker.exists(), "Git executed a submodule's configured filter")

    @unittest.skipIf(os.name == "nt", "Windows cannot create arbitrary non-UTF-8 filename bytes")
    def test_actual_non_utf8_filename_rejected(self):
        raw = os.fsencode(self.repo) + b"/bad-\xff"
        with open(raw, "wb") as stream:
            stream.write(b"fixture")
        self.gate(2)


class ParserTests(unittest.TestCase):
    def test_inspection_environment_disables_trace_and_lazy_fetch(self):
        with mock.patch.dict(os.environ, {"GIT_TRACE": "/trace-file", "GIT_TRACE2_EVENT": "/event-file",
                                         "GIT_NO_LAZY_FETCH": "0"}):
            env = gate_module.git_environment()
        self.assertFalse(any(key.upper().startswith("GIT_TRACE") for key in env))
        self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")

    def test_filter_inventory_record_bound_and_malformed_attributes(self):
        with mock.patch.object(gate_module, "MAX_RECORDS", 1):
            with mock.patch.object(gate_module, "git", side_effect=[b"filter.probe.clean\ncommand\0", b"a\0b\0"]):
                with self.assertRaisesRegex(gate_module.GateError, "filter inventory exceeds"):
                    gate_module.reject_active_filters(Path("."))
        with mock.patch.object(gate_module, "git", side_effect=[b"filter.probe.clean\ncommand\0", b"a\0", b"a\0filter\0"]):
            with self.assertRaisesRegex(gate_module.GateError, "Malformed"):
                gate_module.reject_active_filters(Path("."))

    def test_git_input_is_bounded_before_execution(self):
        with mock.patch.object(gate_module, "MAX_OUTPUT", 1):
            with mock.patch.object(gate_module.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(gate_module.GateError, "input exceeds"):
                    gate_module.git(Path("."), "check-attr", input_data=b"ab")
                launch.assert_not_called()
    def test_help_closed_stdout_returns_two(self):
        p = subprocess.Popen([sys.executable, str(HERE / "git_scope_gate.py"), "--help"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p.stdout.close()
        code = p.wait(timeout=10)
        errors = p.stderr.read()
        p.stderr.close()
        self.assertEqual(code, 2)
        self.assertEqual(errors, b"")

    def test_malformed_or_unsupported_status_fails(self):
        for raw in (b"?? x", b"xx x\0", b"UU x\0", b"AA x\0", b"DD x\0", b"DA x\0", b"RR x\0", b"  x\0",
                    b"R  dest\0", b"?? \xff\0", b"?? /absolute\0", b"?? src/\0", b"\0"):
            with self.subTest(raw=raw), self.assertRaises(gate_module.GateError):
                gate_module.parse_status(raw)

    def test_rename_order_and_control_path_escaping(self):
        records = gate_module.parse_status(b"R  src/a\tb\nc\0old name\0")
        self.assertEqual(records[0]["paths"], ["src/a\tb\nc", "old name"])
        result = {"result": "clean", "records": records, "violations": [], "reasons": []}
        rendered = gate_module.render(result, "text")
        self.assertIn('"src/a\\tb\\nc"', rendered)

    def test_matching_has_boundaries_and_case(self):
        self.assertTrue(gate_module.matches("src/a", "src/"))
        self.assertFalse(gate_module.matches("src-other/a", "src/"))
        self.assertFalse(gate_module.matches("src", "src/"))
        self.assertFalse(gate_module.matches("Src/a", "src/"))
        self.assertFalse(gate_module.matches("file/child", "file"))

    def test_status_record_limit(self):
        with mock.patch.object(gate_module, "MAX_RECORDS", 1):
            with self.assertRaises(gate_module.GateError):
                gate_module.parse_status(b"?? a\0?? b\0")

    def test_policy_size_and_entry_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            policy.write_bytes(b" " * (gate_module.MAX_POLICY + 1))
            with self.assertRaises(gate_module.GateError):
                gate_module.load_policy(policy)
            policy.write_text(json.dumps({"allow": ["src/"] * 4097}), encoding="utf-8")
            with self.assertRaises(gate_module.GateError):
                gate_module.load_policy(policy)

    def test_temp_capture_is_bounded(self):
        # A real Git response larger than a deliberately lowered cap.
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "-C", directory, "init", "-q"], check=True, capture_output=True)
            with mock.patch.object(gate_module, "MAX_OUTPUT", 1):
                with self.assertRaisesRegex(gate_module.GateError, "exceeds"):
                    gate_module.git(Path(directory), "rev-parse", "--show-toplevel")

    def test_timeout_is_error(self):
        process = mock.Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("git", 30), 0]
        with mock.patch.object(gate_module.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(gate_module.GateError, "timed out"):
                gate_module.git(Path("."), "status")
        process.kill.assert_called_once()


if __name__ == "__main__":
    unittest.main()
