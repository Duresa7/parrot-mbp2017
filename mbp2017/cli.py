"""Command line and setup wizard."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TextIO

from . import NAME, __version__
from .fixes import all_fixes
from .fixes.base import Context, Fix, FixError, Health, Options, State, Status
from .hardware import probe, summary_rows, to_dict
from .system import CommandError, System
from .ui import UI

EXPECTED_ERRORS = (FixError, CommandError, OSError, ValueError)


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)

    def _print_message(self, message: str, file: TextIO | None = None) -> None:
        if message:
            self.output.write(message)


def _parser(out: TextIO) -> argparse.ArgumentParser:
    def common(parser: argparse.ArgumentParser) -> None:
        for name in ("yes", "dry-run", "force", "no-color", "json"):
            parser.add_argument("--" + name, action="store_true", default=argparse.SUPPRESS)
        for name in ("only", "skip", "debs", "backup-dir"):
            parser.add_argument("--" + name, default=argparse.SUPPRESS)
        parser.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        parser.add_argument("--version", action="version", version=f"{NAME} {__version__}")

    parser = _Parser(prog=NAME, description="Reversible setup for 2016–2017 MacBook Pros.")
    parser.output = out
    common(parser)
    commands = parser.add_subparsers(dest="command")
    for name in ("setup", "status", "detect", "install", "remove", "restore-t1", "list"):
        child = commands.add_parser(name)
        child.output = out
        common(child)
        if name in ("install", "remove"):
            child.add_argument("fixes", nargs="+")
        if name == "restore-t1":
            source = child.add_mutually_exclusive_group(required=True)
            source.add_argument("--from", dest="source")
            source.add_argument("--online", action="store_true")
    return parser


def _status(fix: Fix, ctx: Context) -> Status:
    reason = fix.gate(ctx.hw)
    return Status(State.NOT_NEEDED, reason) if reason else fix.status(ctx)


def _statuses(fixes: list[Fix], ctx: Context) -> dict[str, Status]:
    statuses = {}
    for fix in fixes:
        try:
            statuses[fix.id] = _status(fix, ctx)
        except EXPECTED_ERRORS as exc:
            statuses[fix.id] = Status(State.BLOCKED, f"Could not check: {exc}")
    for fix in fixes:
        if statuses[fix.id].state == State.NOT_NEEDED:
            continue
        missing = [name for name in fix.requires
                   if statuses.get(name, Status(State.TODO)).state != State.DONE
                   and name not in ctx.options.selected]
        if missing:
            statuses[fix.id] = Status(State.BLOCKED, "Needs " + ", ".join(missing) + " first.")
    return statuses


def _fix_table(fixes: list[Fix], statuses: dict[str, Status], ui: UI) -> None:
    ui.table(("#", "Fix", "Status", "Summary"),
             [(index, fix.id, statuses[fix.id].state.value, fix.summary)
              for index, fix in enumerate(fixes, 1)])
    for fix in fixes:
        if statuses[fix.id].detail:
            ui.detail(f"{fix.id}: {statuses[fix.id].detail}")


def _warnings(ctx: Context) -> None:
    if not ctx.hw.supported:
        ctx.ui.warn("This hardware is not supported. Changes need --force.")
    elif not ctx.hw.tested:
        ctx.ui.warn("This model has not been tested yet.")
    if not ctx.hw.is_parrot:
        ctx.ui.warn("This system is not Parrot OS. These fixes were tested on Parrot OS 7.")
    if not ctx.hw.debian13_based:
        ctx.ui.warn("This system is not a supported Debian 13 based distribution.")
    if ctx.hw.t1_state == "recovery":
        ctx.ui.warn("The T1 is in recovery mode. Restore its firmware before using the Touch Bar.")


def _health(fixes: list[Fix], ctx: Context) -> list[Health]:
    missing = [kernel for kernel, present in ctx.hw.headers.items() if not present]
    checks = [Health("warn", "Kernel headers are missing for: " + ", ".join(missing)
                     + ". Install them so drivers can be built for these kernels.") if missing else
              Health("ok", "Kernel headers installed for every kernel in /lib/modules.")]
    for fix in fixes:
        try:
            checks.extend(fix.health(ctx))
        except EXPECTED_ERRORS as exc:
            checks.append(Health("error", f"Could not check {fix.id}: {exc}"))
    return checks


def _apply(fixes: list[Fix], ctx: Context, *, remove: bool = False) -> int:
    changed, failures, notes, after, completed = [], [], [], set(), set()
    by_id = {fix.id: fix for fix in fixes}
    for fix in reversed(fixes) if remove else fixes:
        if fix.id not in ctx.options.selected:
            continue
        try:
            if not remove:
                state = _status(fix, ctx)
                if state.state == State.NOT_NEEDED:
                    ctx.ui.info(f"{fix.id}: {state.state.value}. {state.detail}".rstrip())
                    continue
                if state.state == State.BLOCKED:
                    raise FixError(state.detail or "This fix is blocked. Check status for details.")
                for name in fix.requires:
                    if name not in completed and (name not in by_id or _status(by_id[name], ctx).state != State.DONE):
                        raise FixError(f"Apply {name} successfully before {fix.id}.")
                if state.state == State.DONE:
                    ctx.ui.info(f"{fix.id}: done.")
                    completed.add(fix.id)
                    continue
            ctx.ui.step(("Removing " if remove else "Setting up ") + fix.title)
            ctx.ui.detail(fix.why)
            notes.extend((fix.remove(ctx) if remove else fix.install(ctx)) or [])
            changed.append(fix.id)
            completed.add(fix.id)
            after.add(fix.after)
        except EXPECTED_ERRORS as exc:
            failures.append(fix.id)
            ctx.ui.error(f"{fix.id}: {exc}")
            ctx.ui.detail("Check the message above, then retry this fix. Other fixes will continue.")
    ctx.ui.heading("Summary")
    ctx.ui.info(("Would change: " if ctx.options.dry_run else "Changed: ") + (", ".join(changed) or "none"))
    ctx.ui.info("Failures: " + (", ".join(failures) or "none"))
    for note in dict.fromkeys(notes):
        ctx.ui.info(note)
    if "reboot" in after:
        ctx.ui.info("Reboot now to finish applying these changes." if not ctx.options.dry_run
                    else "After applying these changes: Reboot now to finish setup.")
    elif "relogin" in after:
        ctx.ui.info("To finish applying these changes, log out and back in." if not ctx.options.dry_run
                    else "After applying these changes, log out and back in.")
    return 1 if failures else 0


def main(argv: list[str] | None = None, *, system: System | None = None,
         stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    out = sys.stdout if stdout is None else stdout
    try:
        return _main(argv, system=system, stdin=stdin, stdout=out)
    except KeyboardInterrupt:
        print("Stopped. Changes made so far are recorded; run status to see where things stand.", file=out)
        return 130


def _main(argv: list[str] | None = None, *, system: System | None = None,
          stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    inp = sys.stdin if stdin is None else stdin
    out = sys.stdout if stdout is None else stdout
    parser = _parser(out)
    defaults = argparse.Namespace(yes=False, dry_run=False, force=False, no_color=False,
                                  json=False, only=None, skip=None, debs=None, backup_dir=None, root="/")
    try:
        args = parser.parse_args(argv, namespace=defaults)
        command = args.command or "setup"
        if args.json and command not in ("status", "detect"):
            raise _UsageError("--json is available for status and detect only.")
        if (args.only is not None or args.skip is not None) and command != "setup":
            raise _UsageError("--only and --skip are setup options.")
    except _UsageError as exc:
        print(f"{NAME}: {exc}", file=out)
        return 2
    except SystemExit as exc:
        return int(exc.code)
    if system is None:
        system = System(root=args.root, dry_run=args.dry_run, out=out)
    else:
        system.dry_run = system.dry_run or args.dry_run
        system.out = out
    ui = UI(color=bool(getattr(out, "isatty", lambda: False)())
            and not args.no_color and "NO_COLOR" not in system.env,
            assume_yes=args.yes, interactive=bool(getattr(inp, "isatty", lambda: False)()),
            out=out, inp=inp)
    try:
        fixes = all_fixes()
        known = {fix.id for fix in fixes}
        selected = set(getattr(args, "fixes", []))
        only = {name.strip() for name in args.only.split(",") if name.strip()} if args.only else None
        skip = {name.strip() for name in args.skip.split(",") if name.strip()} if args.skip else set()
        unknown = (selected | (only or set()) | skip) - known
        if unknown:
            ui.error("Unknown fix: " + ", ".join(sorted(unknown)))
            return 2
        if command == "list":
            for fix in fixes:
                ui.heading(f"{fix.id}: {fix.title}")
                ui.info(fix.summary)
                ui.detail(fix.why)
            return 0
        hw = probe(system)
        ctx = Context(system, hw, ui, Options(args.yes, system.dry_run, args.force,
                                            args.debs, args.backup_dir, selected))
        if command in ("status", "detect"):
            statuses = _statuses(fixes, ctx) if command == "status" else {}
            checks = _health(fixes, ctx) if command == "status" else []
            if args.json:
                payload = to_dict(hw) if command == "detect" else {
                    "hardware": to_dict(hw),
                    "fixes": [{"id": fix.id, "title": fix.title, "state": statuses[fix.id].state.value,
                               "detail": statuses[fix.id].detail, "summary": fix.summary} for fix in fixes],
                    "health": [{"level": check.level, "message": check.message} for check in checks],
                }
                print(json.dumps(payload, indent=2), file=out)
            else:
                ui.table(("Hardware", "Detected"), summary_rows(hw))
                _warnings(ctx)
                if command == "status":
                    _fix_table(fixes, statuses, ui)
                    ui.heading("Health checks")
                    for check in checks:
                        getattr(ui, check.level)(check.message)
            return 0
        if not hw.supported and not args.force:
            ui.error("This hardware is not supported. Use --force only if you want to apply fixes anyway.")
            return 3
        if command == "setup":
            ui.heading(f"{NAME} {__version__}")
            ui.info("Sets up Parrot OS on 2016-2017 Touch Bar MacBook Pros. Every change can be undone.")
            ui.table(("Hardware", "Detected"), summary_rows(hw))
            statuses = _statuses(fixes, ctx)
            _fix_table(fixes, statuses, ui)
        if not system.is_root() and not system.dry_run:
            ui.error("This command needs administrator access. Run it with sudo, or use --dry-run to preview changes.")
            return 1
        if not ui.interactive and not args.yes and not system.dry_run:
            ui.error("No terminal to ask for confirmation. Re-run with --yes to apply without prompts, "
                     "or --dry-run to preview.")
            return 1
        _warnings(ctx)
        if (not hw.is_parrot or not hw.debian13_based) and not ui.confirm("Continue on this distribution?", default=False):
            ui.info("No changes made.")
            return 0
        if command == "restore-t1":
            backup = next((fix for fix in fixes if fix.id == "t1-backup"), None)
            restore = getattr(backup, "restore_t1", None)
            if restore is None:
                ui.error("T1 restore is unavailable: the t1-backup fix is not installed.")
                return 1
            notes = restore(ctx, "online" if args.online else args.source)
            for note in notes or []:
                ui.info(note)
            return 0
        if command == "setup":
            eligible = {fix.id for fix in fixes if not fix.gate(hw) and fix.id not in skip
                        and (only is None or fix.id in only)}
            # Iterate so prerequisites can be selected before their dependents.
            recommended = set()
            for _ in fixes:
                for fix in fixes:
                    if (fix.id in eligible and (fix.default or only is not None)
                            and statuses[fix.id].state != State.DONE
                            and all(name in recommended or statuses.get(name, Status(State.TODO)).state == State.DONE
                                    for name in fix.requires)):
                        recommended.add(fix.id)
            ctx.options.selected = ui.toggle([(fix.id, f"{fix.id}: {fix.title}") for fix in fixes
                                               if fix.id in eligible], recommended)
            if not ctx.options.selected:
                ui.info("Nothing selected. No changes made.")
                return 0
            if not ui.confirm("Apply the selected fixes?"):
                ui.info("No changes made.")
                return 0
        return _apply(fixes, ctx, remove=command == "remove")
    except EXPECTED_ERRORS as exc:
        ui.error(str(exc))
        return 1
