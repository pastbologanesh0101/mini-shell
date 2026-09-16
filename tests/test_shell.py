"""Unit tests for the mini Unix shell (shell.Shell).

Run with either:
    python -m unittest discover -s tests
    pytest
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shell import Shell  # noqa: E402


class ShellTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.shell = Shell()

    def tearDown(self):
        os.chdir(self._orig_cwd)
        self.tmpdir.cleanup()

    def path(self, name):
        return os.path.join(self.tmpdir.name, name)


class TestBuiltins(ShellTestCase):
    def test_cd_changes_directory_and_pwd_reflects_it(self):
        target = self.tmpdir.name
        self.shell.run_line(f"cd {target}")
        self.assertEqual(os.path.realpath(os.getcwd()), os.path.realpath(target))

        outfile = self.path("pwd_out.txt")
        self.shell.run_line(f"pwd > {outfile}")
        with open(outfile) as f:
            content = f.read().strip()
        self.assertEqual(os.path.realpath(content), os.path.realpath(target))

    def test_echo_with_env_var_expansion(self):
        self.shell.env["MY_VAR"] = "hello-world"
        outfile = self.path("echo_out.txt")
        self.shell.run_line(f"echo value=$MY_VAR > {outfile}")
        with open(outfile) as f:
            self.assertEqual(f.read().strip(), "value=hello-world")

    def test_export_sets_env_var_for_children(self):
        self.shell.run_line("export FOO=bar123")
        self.assertEqual(self.shell.env.get("FOO"), "bar123")
        outfile = self.path("export_out.txt")
        self.shell.run_line(f"echo $FOO > {outfile}")
        with open(outfile) as f:
            self.assertEqual(f.read().strip(), "bar123")

    def test_history_builtin_records_commands(self):
        self.shell.run_line("echo one > /dev/null")
        self.shell.run_line("echo two > /dev/null")
        self.assertIn("echo one > /dev/null", self.shell.history)
        self.assertIn("echo two > /dev/null", self.shell.history)


class TestRedirection(ShellTestCase):
    def test_output_redirection_creates_file(self):
        outfile = self.path("created.txt")
        self.assertFalse(os.path.exists(outfile))
        self.shell.run_line(f"echo hello > {outfile}")
        self.assertTrue(os.path.exists(outfile))
        with open(outfile) as f:
            self.assertEqual(f.read().strip(), "hello")

    def test_output_redirection_overwrites(self):
        outfile = self.path("overwrite.txt")
        self.shell.run_line(f"echo first > {outfile}")
        self.shell.run_line(f"echo second > {outfile}")
        with open(outfile) as f:
            content = f.read()
        self.assertEqual(content.strip(), "second")

    def test_output_redirection_appends(self):
        outfile = self.path("append.txt")
        self.shell.run_line(f"echo first > {outfile}")
        self.shell.run_line(f"echo second >> {outfile}")
        with open(outfile) as f:
            lines = f.read().splitlines()
        self.assertEqual(lines, ["first", "second"])

    def test_input_redirection_feeds_stdin(self):
        infile = self.path("input.txt")
        outfile = self.path("output.txt")
        with open(infile, "w") as f:
            f.write("line1\nline2\nline3\n")
        self.shell.run_line(f"cat < {infile} > {outfile}")
        with open(outfile) as f:
            self.assertEqual(f.read(), "line1\nline2\nline3\n")


class TestPipes(ShellTestCase):
    def test_two_stage_pipe(self):
        outfile = self.path("pipe2.txt")
        self.shell.run_line(f"printf 'banana\\napple\\ncherry\\n' | sort > {outfile}")
        with open(outfile) as f:
            self.assertEqual(f.read().splitlines(), ["apple", "banana", "cherry"])

    def test_three_stage_pipe(self):
        outfile = self.path("pipe3.txt")
        self.shell.run_line(
            f"printf 'apple\\nbanana\\navocado\\ncherry\\n' | grep a | sort > {outfile}"
        )
        with open(outfile) as f:
            # "cherry" has no 'a', so grep filters it out; the rest sort alphabetically.
            self.assertEqual(
                f.read().splitlines(), ["apple", "avocado", "banana"]
            )


class TestErrorHandling(ShellTestCase):
    def test_unknown_command_reports_error_without_crashing(self):
        # Should not raise, and the shell should remain usable afterwards.
        self.shell.run_line("this_command_does_not_exist_12345")
        self.assertNotEqual(self.shell.last_exit_code, 0)
        self.assertTrue(self.shell.running)

        outfile = self.path("still_alive.txt")
        self.shell.run_line(f"echo still-alive > {outfile}")
        with open(outfile) as f:
            self.assertEqual(f.read().strip(), "still-alive")


class TestBackgroundJobs(ShellTestCase):
    def test_background_job_does_not_block(self):
        start = time.time()
        self.shell.run_line("sleep 2 &")
        elapsed = time.time() - start
        self.assertLess(elapsed, 1.0, "background job should not block the shell")
        self.assertEqual(len(self.shell.jobs), 1)
        self.assertEqual(self.shell.jobs[0]["status"], "Running")

    def test_jobs_builtin_reports_status(self):
        self.shell.run_line("sleep 0.2 &")
        self.assertEqual(len(self.shell.jobs), 1)
        # Let it finish, then make sure `jobs` notices without raising.
        time.sleep(0.5)
        self.shell.run_line("jobs")
        self.assertEqual(self.shell.jobs[0]["status"], "Done")


if __name__ == "__main__":
    unittest.main()
