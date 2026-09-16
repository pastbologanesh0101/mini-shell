# Unix Shell (mini-shell)

A small, dependency-free Unix-like shell written in pure Python. It
implements the core mechanics of a POSIX shell — built-ins, PATH lookup,
`fork`/`execvp`, pipes, I/O redirection, and background jobs — in about
400 lines of stdlib-only code (`shell.py`).

## Features

- **Built-ins:** `cd`, `pwd`, `exit [code]`, `echo`, `export VAR=value`,
  `history`, `jobs`.
- **External commands:** resolved via `PATH` and run with
  `os.fork()` + `os.execvp()`.
- **Environment variables:** `$VAR`, `${VAR}`, and `$?` (last exit code)
  are expanded in unquoted and double-quoted text; single-quoted text is
  left literal.
- **I/O redirection:** `>` (truncate), `>>` (append), `<` (read stdin
  from a file).
- **Pipes:** `cmd1 | cmd2 | cmd3 | ...` — any number of stages, built-ins
  and external commands can be mixed in a pipeline.
- **Background jobs:** trailing `&` runs a pipeline without blocking the
  shell; `jobs` reports each job's id, PID, status (`Running`/`Done`),
  and original command line.
- **Two run modes:**
  - **Interactive REPL:** `python3 shell.py`
  - **Script mode:** `python3 shell.py myscript.sh` — reads and runs
    commands line by line from a file, with no TTY required. This is
    what makes the shell fully testable in CI.

The parsing and execution logic lives entirely in the `Shell` class
(`shell.py`), independent of the REPL loop, so it can be driven directly:

```python
from shell import Shell

sh = Shell()
sh.run_line("echo hello > out.txt")
sh.run_line("cat < out.txt")
```

## Running it

Interactive:

```console
$ python3 shell.py
mini-shell$ echo hello world
hello world
mini-shell$ exit
```

Script mode:

```console
$ python3 shell.py examples.sh
```

## Example session

```
$ python3 shell.py
mini-shell$ pwd
/home/user/mini-shell
mini-shell$ cd /tmp
mini-shell$ pwd
/tmp
mini-shell$ export NAME=world
mini-shell$ echo hello $NAME
hello world
mini-shell$ printf 'banana\napple\ncherry\n' | sort | head -n 2
apple
banana
mini-shell$ echo logging > log.txt
mini-shell$ echo more >> log.txt
mini-shell$ cat < log.txt
logging
more
mini-shell$ sleep 5 &
[1] 48213
mini-shell$ jobs
[1]+ Running	sleep 5 &
mini-shell$ does_not_exist
does_not_exist: command not found
mini-shell$ history
1  pwd
2  cd /tmp
3  pwd
4  export NAME=world
5  echo hello $NAME
6  printf 'banana\napple\ncherry\n' | sort | head -n 2
7  echo logging > log.txt
8  echo more >> log.txt
9  cat < log.txt
10  sleep 5 &
11  jobs
12  does_not_exist
mini-shell$ exit
```

## How it works

- **Tokenizer** (`Shell._tokenize`): a hand-rolled scanner that handles
  single/double quotes and treats `|`, `<`, `>`, `>>`, `&` as operator
  tokens. Environment variables are expanded per-word, respecting quoting
  rules (no expansion inside single quotes).
- **Parser** (`Shell._split_pipeline`, `Shell._parse_redirections`):
  splits the token stream on `|` into pipeline stages, then pulls
  redirection operators and filenames out of each stage's tokens to
  build a `Command(argv, stdin_file, stdout_file, append)`.
- **Execution:**
  - A single "stateful" built-in (`cd`, `pwd`, `exit`, `export`,
    `history`, `jobs`) run alone and in the foreground executes directly
    in the shell process, so it can actually change the shell's state
    (working directory, environment, history, running flag).
  - Everything else — external commands, `echo` in a pipeline,
    multi-stage pipelines, and anything run in the background — goes
    through `os.fork()`. Each stage's child wires up its pipe ends and
    any file redirections with `os.dup2`, then either calls a built-in
    function directly or calls `os.execvp()`. The parent collects PIDs
    and either waits (foreground) or files the job under `self.jobs`
    (background).
- **Jobs:** background pipelines are tracked with their PIDs and status;
  the shell reaps finished jobs (via non-blocking `os.waitpid(pid,
  os.WNOHANG)`) whenever a new line is run or `jobs` is invoked.

## Known limitations

- No globbing, subshells, `&&`/`||`/`;` command lists, or job control
  signals (`fg`/`bg`/`Ctrl-Z`).
- A stateful built-in (`cd`, `export`, etc.) used as one stage of a
  multi-command pipeline runs in a forked child, so its effect on shell
  state does not persist — this matches how most real shells treat
  built-ins in a pipeline subshell.

## Tests

Unit tests live in `tests/test_shell.py` and use only the standard
library (`unittest`); `pytest` can also run them.

```console
$ python3 -m unittest discover -s tests -v
# or
$ pytest -v
```

They cover: `cd`/`pwd` interaction, `echo` with environment variable
expansion, `export`, `history`, output redirection (create / overwrite /
append), input redirection, 2-stage and 3-stage pipes, unknown-command
error handling, and background jobs (non-blocking + `jobs` status).

Continuous integration (`.github/workflows/tests.yml`) runs the suite on
`ubuntu-latest` for Python 3.11 and 3.12 on every push and pull request.

## License

MIT — see [LICENSE](LICENSE).
