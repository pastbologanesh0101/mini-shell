#!/usr/bin/env python3
"""
Unix Shell (mini-shell) -- a small POSIX-ish shell implemented in pure Python.

Supports:
    - built-ins: cd, pwd, exit, echo, export, history, jobs
    - external command execution (PATH search + os.execvp)
    - I/O redirection: >, >>, <
    - pipes: cmd1 | cmd2 | cmd3 ...
    - background jobs with `&` and a `jobs` built-in to inspect them

The parsing/execution logic lives in the Shell class (see `Shell.run_line`)
so it can be driven directly from tests without needing a real TTY.  The
REPL loop and the script-mode runner are thin wrappers around it.
"""

import contextlib
import os
import re
import sys

PROMPT = "mini-shell$ "

STATEFUL_BUILTINS = {"cd", "pwd", "exit", "export", "history", "jobs"}


class Command:
    """A single parsed command: argv plus any redirections."""

    __slots__ = ("argv", "stdin_file", "stdout_file", "append")

    def __init__(self, argv, stdin_file=None, stdout_file=None, append=False):
        self.argv = argv
        self.stdin_file = stdin_file
        self.stdout_file = stdout_file
        self.append = append

    def __repr__(self):
        return f"Command(argv={self.argv!r}, stdin={self.stdin_file!r}, stdout={self.stdout_file!r}, append={self.append})"


class ShellSyntaxError(Exception):
    pass


class Shell:
    """A small Unix-like shell.

    `run_line(line)` parses and executes a single line of shell input.
    All state (cwd via the OS, environment, history, background jobs) is
    kept on the instance so it can be driven programmatically, e.g. from
    unit tests.
    """

    def __init__(self):
        self.env = os.environ
        self.history = []
        self.jobs = []          # list of dicts: id, pids, cmd, status
        self.running = True
        self.exit_code = 0
        self.last_exit_code = 0
        self._next_job_id = 1
        self.builtins = {
            "cd": self._builtin_cd,
            "pwd": self._builtin_pwd,
            "exit": self._builtin_exit,
            "echo": self._builtin_echo,
            "export": self._builtin_export,
            "history": self._builtin_history,
            "jobs": self._builtin_jobs,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_line(self, line):
        """Parse and execute one line of shell input."""
        self._reap_finished_jobs()

        raw = line.rstrip("\n")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            return
        self.history.append(stripped)

        try:
            tokens = self._tokenize(raw)
        except ShellSyntaxError as e:
            sys.stderr.write(f"mini-shell: syntax error: {e}\n")
            return

        if not tokens:
            return

        background = False
        if tokens[-1] == "&":
            background = True
            tokens = tokens[:-1]

        try:
            segments = self._split_pipeline(tokens)
            commands = [self._parse_redirections(seg) for seg in segments]
        except ShellSyntaxError as e:
            sys.stderr.write(f"mini-shell: syntax error: {e}\n")
            return

        commands = [c for c in commands if c.argv]
        if not commands:
            return

        if len(commands) == 1 and not background and commands[0].argv[0] in STATEFUL_BUILTINS:
            self._run_stateful_builtin(commands[0])
            return

        self._run_pipeline(commands, background, raw_cmd=stripped)

    # ------------------------------------------------------------------
    # Tokenizing / parsing
    # ------------------------------------------------------------------

    def _expand(self, text):
        def repl(m):
            if m.group("brace"):
                return self.env.get(m.group("brace"), "")
            if m.group("name"):
                return self.env.get(m.group("name"), "")
            if m.group("q"):
                return str(self.last_exit_code)
            return ""

        pattern = re.compile(
            r"\$\{(?P<brace>[A-Za-z_][A-Za-z0-9_]*)\}"
            r"|\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
            r"|\$(?P<q>\?)"
        )
        return pattern.sub(repl, text)

    def _tokenize(self, line):
        """Turn a raw line into a flat list of tokens.

        Operators '|', '<', '>', '>>', '&' are returned as their own
        tokens; everything else is a "word" token with environment
        variables expanded (unless single-quoted).
        """
        tokens = []
        parts = []  # accumulated (text, is_single_quoted) for the current word
        i = 0
        n = len(line)

        def flush_word():
            if parts:
                word = "".join(
                    text if quoted else self._expand(text) for text, quoted in parts
                )
                tokens.append(word)
                parts.clear()

        while i < n:
            c = line[i]
            if c == "'":
                j = line.find("'", i + 1)
                if j == -1:
                    raise ShellSyntaxError("unterminated single quote")
                parts.append((line[i + 1 : j], True))
                i = j + 1
                continue
            if c == '"':
                j = line.find('"', i + 1)
                if j == -1:
                    raise ShellSyntaxError("unterminated double quote")
                parts.append((line[i + 1 : j], False))
                i = j + 1
                continue
            if c.isspace():
                flush_word()
                i += 1
                continue
            if c in "|<>&":
                flush_word()
                if c == ">" and i + 1 < n and line[i + 1] == ">":
                    tokens.append(">>")
                    i += 2
                else:
                    tokens.append(c)
                    i += 1
                continue
            j = i
            buf = []
            while j < n and (not line[j].isspace()) and line[j] not in "|<>&'\"":
                buf.append(line[j])
                j += 1
            parts.append(("".join(buf), False))
            i = j

        flush_word()
        return tokens

    def _split_pipeline(self, tokens):
        segments = [[]]
        for t in tokens:
            if t == "|":
                segments.append([])
            else:
                segments[-1].append(t)
        for seg in segments:
            if not seg:
                raise ShellSyntaxError("empty command in pipeline")
        return segments

    def _parse_redirections(self, tokens):
        argv = []
        stdin_file = None
        stdout_file = None
        append = False
        i = 0
        n = len(tokens)
        while i < n:
            t = tokens[i]
            if t in ("<", ">", ">>"):
                if i + 1 >= n:
                    raise ShellSyntaxError(f"expected filename after '{t}'")
                filename = tokens[i + 1]
                if t == "<":
                    stdin_file = filename
                elif t == ">":
                    stdout_file = filename
                    append = False
                else:
                    stdout_file = filename
                    append = True
                i += 2
                continue
            argv.append(t)
            i += 1
        return Command(argv, stdin_file, stdout_file, append)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _run_stateful_builtin(self, cmd):
        """Run a builtin that must execute in *this* process (cd, exit, ...)."""
        saved = {}
        try:
            if cmd.stdin_file:
                fd = os.open(cmd.stdin_file, os.O_RDONLY)
                saved[0] = os.dup(0)
                os.dup2(fd, 0)
                os.close(fd)
            if cmd.stdout_file:
                flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if cmd.append else os.O_TRUNC)
                fd = os.open(cmd.stdout_file, flags, 0o644)
                sys.stdout.flush()
                saved[1] = os.dup(1)
                os.dup2(fd, 1)
                os.close(fd)
        except OSError as e:
            sys.stderr.write(f"mini-shell: {e.filename}: {e.strerror}\n")
            for fdnum, s in saved.items():
                os.dup2(s, fdnum)
                os.close(s)
            self.last_exit_code = 1
            return

        try:
            self.builtins[cmd.argv[0]](cmd.argv)
            self.last_exit_code = 0
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 - never let a builtin crash the shell
            sys.stderr.write(f"mini-shell: {cmd.argv[0]}: {e}\n")
            self.last_exit_code = 1
        finally:
            sys.stdout.flush()
            for fdnum, s in saved.items():
                os.dup2(s, fdnum)
                os.close(s)

    def _run_pipeline(self, commands, background, raw_cmd):
        n = len(commands)
        pipes = [os.pipe() for _ in range(n - 1)]
        pids = []

        for idx, cmd in enumerate(commands):
            pid = os.fork()
            if pid == 0:
                self._child_exec(cmd, idx, n, pipes)
                os._exit(127)  # pragma: no cover - _child_exec never returns
            pids.append(pid)

        for r, w in pipes:
            os.close(r)
            os.close(w)

        if background:
            job_id = self._next_job_id
            self._next_job_id += 1
            self.jobs.append(
                {"id": job_id, "pids": pids, "cmd": raw_cmd, "status": "Running"}
            )
            print(f"[{job_id}] {pids[-1]}")
            self.last_exit_code = 0
        else:
            status = 0
            for pid in pids:
                _, wstatus = os.waitpid(pid, 0)
                if os.WIFEXITED(wstatus):
                    status = os.WEXITSTATUS(wstatus)
                elif os.WIFSIGNALED(wstatus):
                    status = 128 + os.WTERMSIG(wstatus)
            self.last_exit_code = status

    def _child_exec(self, cmd, idx, n, pipes):
        """Runs inside the forked child; never returns normally."""
        try:
            if idx > 0:
                os.dup2(pipes[idx - 1][0], 0)
            if idx < n - 1:
                os.dup2(pipes[idx][1], 1)
            for r, w in pipes:
                os.close(r)
                os.close(w)

            if cmd.stdin_file:
                fd = os.open(cmd.stdin_file, os.O_RDONLY)
                os.dup2(fd, 0)
                os.close(fd)
            if cmd.stdout_file:
                flags = os.O_WRONLY | os.O_CREAT | (
                    os.O_APPEND if cmd.append else os.O_TRUNC
                )
                fd = os.open(cmd.stdout_file, flags, 0o644)
                os.dup2(fd, 1)
                os.close(fd)
        except OSError as e:
            sys.stderr.write(f"mini-shell: {e.filename}: {e.strerror}\n")
            os._exit(1)

        name = cmd.argv[0]
        if name in self.builtins:
            try:
                self.builtins[name](cmd.argv)
                sys.stdout.flush()
                os._exit(0)
            except SystemExit as e:
                os._exit(e.code or 0)
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"mini-shell: {name}: {e}\n")
                os._exit(1)
        else:
            try:
                os.execvp(name, cmd.argv)
            except FileNotFoundError:
                sys.stderr.write(f"{name}: command not found\n")
                os._exit(127)
            except PermissionError:
                sys.stderr.write(f"{name}: permission denied\n")
                os._exit(126)
            except OSError as e:
                sys.stderr.write(f"{name}: {e}\n")
                os._exit(1)

    def _reap_finished_jobs(self):
        for job in self.jobs:
            self._refresh_job(job)

    def _refresh_job(self, job):
        if job["status"] == "Done":
            return
        reaped = job.setdefault("_reaped", set())
        all_done = True
        for pid in job["pids"]:
            if pid in reaped:
                continue
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                wpid = pid
            if wpid == 0:
                all_done = False
            else:
                reaped.add(pid)
        if all_done:
            job["status"] = "Done"

    # ------------------------------------------------------------------
    # Built-ins
    # ------------------------------------------------------------------

    def _builtin_cd(self, argv):
        target = argv[1] if len(argv) > 1 else self.env.get("HOME", "/")
        try:
            os.chdir(target)
            self.env["PWD"] = os.getcwd()
        except FileNotFoundError:
            sys.stderr.write(f"cd: no such file or directory: {target}\n")
            self.last_exit_code = 1
        except NotADirectoryError:
            sys.stderr.write(f"cd: not a directory: {target}\n")
            self.last_exit_code = 1
        except PermissionError:
            sys.stderr.write(f"cd: permission denied: {target}\n")
            self.last_exit_code = 1

    def _builtin_pwd(self, argv):
        print(os.getcwd())

    def _builtin_exit(self, argv):
        code = 0
        if len(argv) > 1:
            try:
                code = int(argv[1])
            except ValueError:
                code = 0
        self.running = False
        self.exit_code = code
        raise SystemExit(code)

    def _builtin_echo(self, argv):
        print(" ".join(argv[1:]))

    def _builtin_export(self, argv):
        for arg in argv[1:]:
            if "=" in arg:
                key, _, value = arg.partition("=")
                self.env[key] = value
            else:
                self.env.setdefault(arg, "")

    def _builtin_history(self, argv):
        for i, entry in enumerate(self.history, start=1):
            print(f"{i}  {entry}")

    def _builtin_jobs(self, argv):
        self._reap_finished_jobs()
        for job in self.jobs:
            marker = "+" if job["status"] == "Running" else " "
            print(f"[{job['id']}]{marker} {job['status']}\t{job['cmd']}")


# ----------------------------------------------------------------------
# REPL / script-mode entry point
# ----------------------------------------------------------------------


def run_repl(shell):
    while shell.running:
        try:
            line = input(PROMPT)
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            continue
        try:
            shell.run_line(line)
        except SystemExit:
            break


def run_script(shell, path):
    with open(path, "r") as f:
        for line in f:
            if not shell.running:
                break
            try:
                shell.run_line(line)
            except SystemExit:
                break


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    shell = Shell()
    if argv:
        run_script(shell, argv[0])
    else:
        run_repl(shell)
    return shell.exit_code


if __name__ == "__main__":
    sys.exit(main())
