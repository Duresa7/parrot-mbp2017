# Framework interfaces

Add each fix in a new module under `mbp2017/fixes/`. Define a `Fix` subclass
with its own unique `id`; `all_fixes()` discovers it automatically. The known
IDs follow `ORDER`; other IDs follow alphabetically. `get_fix(id)` returns an
instance or `None`. Absent fix modules need no placeholders or registry edits.
An import error inside a present module is reported rather than hidden.

`Context` supplies `system`, `hw`, `ui`, and `options`. A fix's `gate(hw)` returns
an absence reason or `None`. Keep `status(ctx)` read-only. `install(ctx)` and
`remove(ctx)` return lists of next-step messages; raise `FixError` for a failure
the owner can act on. Declare prerequisites in `requires`, and use `after` for
`reboot` or `relogin`. The CLI checks prerequisites again during application,
continues independent fixes after failures, and returns exit code 1 if any fail.

For declarative changes, subclass `FileFix` and define `files` with
`ManagedFile(path, data_name, mode=0o644, when=...)` and/or `blocks` with
`ManagedBlock(path, data_name, when=...)`. Both tuples and lists work. Assets
are read verbatim as UTF-8 text by `System.data_text()` from `mbp2017/data/`.
The input fix is the working example. Hooks `after_install(ctx)` and
`after_remove(ctx)` return next-step lists and run only when file content
changes. Removal uses ownership records even if hardware is no longer present.

All target filesystem access and commands belong in `System`. Use system
absolute paths, including for `run_streamed`'s `cwd` and `log_path`.
`System.path()` translates a target path to a host `Path`; command arguments
are passed verbatim, so use an injected runner when exercising commands on a
fixture root. `System.kernel_release()` supplies the running kernel; mock it
when a test needs a fixed release. Probing installed kernels uses the fixture's
`/lib/modules` independently.

A runner has the signature `(argv: list[str], opts: dict) -> Result`.
`run` supplies `input`, merged `env`, and `timeout`; `run_streamed` supplies
merged `env` and rooted `cwd`. Stream callbacks receive lines without their
newline terminators. Mark modifying commands `mutating=True`; dry runs skip
them. All streamed commands are skipped in dry-run mode. Timeouts from the
default `run` runner become command failures with code 124; a missing
executable uses code 127.

The manifest is a JSON object keyed by fix ID, with `files` and `notes` objects
inside each entry. Treat this layout as private: use `owned_paths`, `note`,
`notes`, and `clear_notes`. Ownership is retained across process restarts.
Files with identical unowned content are recorded as preexisting and retained
on removal. If a later install changes such a file, its previous bytes and mode
are backed up first. Managed blocks preserve the surrounding text, including
line endings and whether the original ended in a newline. Distinct fixes may
own separate blocks in one file, but whole-file ownership is exclusive.
Unreadable or damaged manifests stop mutations instead of discarding history.

`main(argv=None, *, system=None, stdin=None, stdout=None)` returns an exit code.
Options work before or after the command. Setup's `--only` and `--skip` take
comma-separated IDs; explicitly naming an opt-in fix with `--only` selects it.
Read-only commands remain available on unsupported hardware. Changes require
`--force` on unsupported hardware and confirmation on other distributions;
`--yes` accepts that confirmation. `detect --json` emits hardware fields
and derived properties directly. `status --json` emits `hardware`, `fixes`
(each with `id`, `title`, `state`, `detail`, `summary`), and `health`.
For the future T1 backup fix, implement `restore_t1(ctx, source)` as a method:
`source` is the backup directory or the string `"online"`; return next-step
messages as a list.

Tests use `make_mac(tmpdir, **overrides)` and `FakeRunner` from `tests/helpers.py`.
The helper documents fixture options. Script `FakeRunner` with tuple argv
prefixes mapped to a `Result` or a list of sequential results; the longest
prefix wins. Inspect `.calls` for argv lists and `.options` for runner options.
Use a temporary directory inside the repository when running these tests.

Run checks from the repository root:

```sh
python3 -m unittest discover -s tests
python3 -m compileall -q mbp2017
```
