# Coding standards

- Python 3.11+, standard library only. Type hints on public functions.
- All filesystem access and commands go through `mbp2017.system.System`. No
  direct `open()`, `subprocess` or `os` calls that touch the live system outside
  that module. This keeps `--dry-run` honest and the tests hermetic.
- A fix is a `Fix` subclass in `mbp2017/fixes/`. It owns its hardware gate,
  status check, install and remove. Shared behaviour belongs in the base class
  or `System`, not copied between fixes.
- Files a fix installs live verbatim in `mbp2017/data/` and start with
  `# Installed by parrot-mbp2017 (<fix id>).`
- User-facing text is plain English: say what a step does and why before doing
  it. No unexplained jargon.
- Errors a user can act on are raised as `FixError` with a message that says
  what to do next. Never print a stack trace for an expected failure.
- Tests use `unittest`, run offline and unprivileged, and build fixture trees
  under a temp dir.
- Shell scripts use `set -euo pipefail`, tabs for indentation, and pass
  `shellcheck`.
- Commits carry no AI attribution trailers.
