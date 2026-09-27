"""The filesystem and process boundary; all system paths are rooted here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import copy
import glob as glob_module
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Callable, TextIO


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandError(Exception):
    def __init__(self, argv: list[str], result: Result):
        self.argv = list(argv)
        self.result = result
        tail = "\n".join(result.stderr.splitlines()[-5:])
        super().__init__(f"{shlex.join(argv)} failed (exit {result.returncode})"
                         + (f":\n{tail}" if tail else "."))


@dataclass(frozen=True)
class User:
    name: str
    uid: int
    gid: int
    home: str


class System:
    STATE_DIR = "/var/lib/parrot-mbp2017"
    MANIFEST = STATE_DIR + "/manifest.json"
    REPLACED = STATE_DIR + "/replaced"

    def __init__(self, root: str = "/", *, dry_run: bool = False,
                 runner: Callable | None = None, env: dict | None = None,
                 euid: int | None = None, out: TextIO | None = None):
        self.root = Path(root).absolute()
        self.dry_run = dry_run
        self.runner = runner
        self.env = dict(os.environ if env is None else env)
        # dkms, update-initramfs and friends live in sbin, which a normal
        # user's PATH leaves out; status must still find them.
        path = [entry for entry in self.env.get("PATH", "").split(":") if entry]
        self.env["PATH"] = ":".join(path + [entry for entry in ("/usr/local/sbin", "/usr/sbin", "/sbin")
                                            if entry not in path])
        self.euid = os.geteuid() if euid is None else euid
        self.out = sys.stdout if out is None else out

    def path(self, p: str) -> Path:
        value = PurePosixPath(p)
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError(f"Expected an absolute system path without '..': {p}")
        return self.root.joinpath(*value.parts[1:])

    def exists(self, p: str) -> bool:
        return self.path(p).exists()

    def is_dir(self, p: str) -> bool:
        return self.path(p).is_dir()

    def read_text(self, p: str, default: str | None = None) -> str | None:
        try:
            with self.path(p).open(encoding="utf-8", newline="") as stream:
                return stream.read()
        except (FileNotFoundError, PermissionError, IsADirectoryError, UnicodeDecodeError):
            return default

    def read_bytes(self, p: str) -> bytes:
        return self.path(p).read_bytes()

    def listdir(self, p: str) -> list[str]:
        try:
            return sorted(child.name for child in self.path(p).iterdir())
        except OSError:
            return []

    def glob(self, pattern: str) -> list[str]:
        return sorted("/" + str(Path(p).relative_to(self.root))
                      for p in glob_module.glob(str(self.path(pattern))))

    def readlink_name(self, p: str) -> str | None:
        try:
            return Path(os.readlink(self.path(p))).name
        except OSError:
            return None

    def which(self, name: str) -> str | None:
        for directory in ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin",
                          "/usr/bin", "/sbin", "/bin"):
            candidate = f"{directory}/{name}"
            if self.path(candidate).is_file() and os.access(self.path(candidate), os.X_OK):
                return candidate
        return None

    def is_root(self) -> bool:
        return self.euid == 0

    def invoking_user(self) -> User | None:
        name = self.env.get("SUDO_USER")
        for line in (self.read_text("/etc/passwd", "") or "").splitlines():
            fields = line.split(":")
            if len(fields) < 7:
                continue
            try:
                uid, gid = int(fields[2]), int(fields[3])
            except ValueError:
                continue
            if (name and fields[0] == name) or (not name and not self.is_root() and uid == self.euid):
                return User(fields[0], uid, gid, fields[5])
        return None

    def kernel_release(self) -> str:
        return os.uname().release

    def data_text(self, name: str) -> str:
        """Read a bundled asset, independent of the target system root."""
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Data names must stay inside mbp2017/data.")
        return (Path(__file__).parent / "data" / name).read_text(encoding="utf-8")

    def _would(self, message: str) -> None:
        print(f"would {message}", file=self.out)

    def run(self, argv: list[str], *, check: bool = True, input: str | None = None,
            env: dict | None = None, timeout: float | None = None,
            mutating: bool = False) -> Result:
        argv = [str(arg) for arg in argv]
        self.log("run: " + shlex.join(argv))
        if self.dry_run and mutating:
            self._would("run: " + shlex.join(argv))
            return Result(0)
        opts = {"input": input, "env": self.env | (env or {}), "timeout": timeout}
        if self.runner is not None:
            result = self.runner(argv, opts)
        else:
            try:
                completed = subprocess.run(argv, capture_output=True, text=True, **opts)
                result = Result(completed.returncode, completed.stdout, completed.stderr)
            except FileNotFoundError as exc:
                result = Result(127, stderr=str(exc))
            except subprocess.TimeoutExpired as exc:
                result = Result(124, stderr=f"Command timed out after {exc.timeout} seconds.")
        if check and not result.ok:
            raise CommandError(argv, result)
        return result

    def run_streamed(self, argv: list[str], *, log_path: str,
                     on_line: Callable[[str], None] | None = None,
                     env: dict | None = None, cwd: str | None = None,
                     mutating: bool = True) -> int:
        argv = [str(arg) for arg in argv]
        if self.dry_run:
            self._would("run: " + shlex.join(argv))
            return 0
        self.log("run: " + shlex.join(argv))
        target = self.path(log_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        opts = {"env": self.env | (env or {}),
                "cwd": str(self.path(cwd)) if cwd else None}
        with target.open("a", encoding="utf-8") as log:
            def emit(line: str) -> None:
                log.write(line)
                log.flush()
                if on_line:
                    on_line(line.rstrip("\r\n"))
            if self.runner is not None:
                result = self.runner(argv, opts)
                for line in (result.stdout + result.stderr).splitlines(keepends=True):
                    emit(line)
                return result.returncode
            try:
                with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, **opts) as process:
                    for line in process.stdout:
                        emit(line)
                    return process.wait()
            except FileNotFoundError as exc:
                emit(str(exc) + "\n")
                return 127

    def _manifest(self) -> dict:
        # Corrupt manifests must fail closed, never silently discard ownership.
        try:
            manifest = json.loads(self.read_bytes(self.MANIFEST))
        except FileNotFoundError:
            return {}
        except PermissionError:
            raise PermissionError(f"{self.MANIFEST} is readable by root only. Run this with sudo.") from None
        if not isinstance(manifest, dict) or any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("files"), dict)
            or not isinstance(entry.get("notes"), dict)
            for entry in manifest.values()
        ):
            raise ValueError("The change manifest is damaged. Restore it from a backup before continuing.")
        return manifest

    def _atomic(self, path: str, content: bytes, mode: int) -> None:
        target = self.path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".parrot-mbp2017-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, mode)
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def _save(self, manifest: dict) -> None:
        if not self.dry_run:
            # Paths and checksums only, nothing private: readable so status works without sudo.
            self._atomic(self.MANIFEST, (json.dumps(manifest, indent=2) + "\n").encode(), 0o644)

    @staticmethod
    def _entry(manifest: dict, fix_id: str) -> dict:
        return manifest.setdefault(fix_id, {"files": {}, "notes": {}})

    @staticmethod
    def _bytes(content: str | bytes) -> bytes:
        return content.encode("utf-8") if isinstance(content, str) else content

    def file_is_current(self, path: str, content: str | bytes) -> bool:
        try:
            return self.read_bytes(path) == self._bytes(content)
        except (FileNotFoundError, PermissionError):
            return False

    def install_file(self, fix_id: str, path: str, content: str | bytes,
                     mode: int = 0o644) -> bool:
        data = self._bytes(content)
        manifest = self._manifest()
        files = self._entry(manifest, fix_id)["files"]
        for owner, entry in manifest.items():
            if path in entry["files"] and (owner != fix_id or entry["files"][path]["kind"] != "file"):
                raise ValueError(f"{path} is already managed by {owner}.")
        current = self.file_is_current(path, data)
        if self.dry_run:
            if not current:
                self._would(f"install file: {path}")
            return not current
        if path not in files or (files[path].get("preexisting") and not current):
            record = {"kind": "file", "preexisting": current}
            if self.exists(path) and not current:
                backup = self.REPLACED + path
                record.update(backup=backup, mode=self.path(path).stat().st_mode & 0o7777)
                self._atomic(backup, self.read_bytes(path), record["mode"])
            files[path] = record
            # Save recovery information before replacing an existing file.
            self._save(manifest)
        if not current:
            self._atomic(path, data, mode)
            self.log(f"{fix_id}: installed {path}")
            self._save(manifest)
        return not current

    def remove_file(self, fix_id: str, path: str) -> bool:
        manifest = self._manifest()
        files = self._entry(manifest, fix_id)["files"]
        record = files.get(path)
        if not record or record["kind"] != "file":
            return False
        changed = not record.get("preexisting", False)
        if self.dry_run:
            if changed:
                self._would(f"restore or remove file: {path}")
            return changed
        if "backup" in record:
            self._atomic(path, self.read_bytes(record["backup"]), record["mode"])
        elif changed:
            self.path(path).unlink(missing_ok=True)
        del files[path]
        self._save(manifest)
        if "backup" in record:
            self.path(record["backup"]).unlink(missing_ok=True)
        self.log(f"{fix_id}: released {path}")
        return changed

    @staticmethod
    def _block(fix_id: str, text: str) -> str:
        return (f"# >>> parrot-mbp2017 {fix_id} >>>\n" + text.rstrip("\n")
                + f"\n# <<< parrot-mbp2017 {fix_id} <<<\n")

    @staticmethod
    def _span(fix_id: str, content: str) -> tuple[int, int] | None:
        start_marker = f"# >>> parrot-mbp2017 {fix_id} >>>"
        end_marker = f"# <<< parrot-mbp2017 {fix_id} <<<"
        lines = content.splitlines(keepends=True)
        offset, start = 0, None
        spans = []
        for line in lines:
            marker = line.rstrip("\r\n")
            if marker == start_marker:
                if start is not None:
                    raise ValueError(f"Repeated managed block marker for {fix_id}.")
                start = offset
            if marker == end_marker:
                if start is None:
                    raise ValueError(f"Incomplete managed block for {fix_id}.")
                spans.append((start, offset + len(line)))
                start = None
            offset += len(line)
        if start is not None or len(spans) > 1:
            raise ValueError(f"Incomplete or repeated managed block for {fix_id}.")
        return spans[0] if spans else None

    def block_is_current(self, fix_id: str, path: str, text: str) -> bool:
        content = self.read_text(path, "")
        span = self._span(fix_id, content)
        return bool(span and content[span[0]:span[1]] == self._block(fix_id, text))

    def _text_for_edit(self, path: str) -> str:
        # An unreadable file is not an empty file: never overwrite it blindly.
        try:
            return self.read_bytes(path).decode("utf-8")
        except FileNotFoundError:
            return ""

    def install_block(self, fix_id: str, path: str, text: str) -> bool:
        content = self._text_for_edit(path)
        span = self._span(fix_id, content)
        block = self._block(fix_id, text)
        separator = "\n" if content and not content.endswith("\n") else ""
        updated = content[:span[0]] + block + content[span[1]:] if span else content + separator + block
        manifest = self._manifest()
        files = self._entry(manifest, fix_id)["files"]
        for entry in manifest.values():
            if entry["files"].get(path, {}).get("kind") == "file":
                raise ValueError(f"{path} is managed as a whole file.")
        if self.dry_run:
            if updated != content:
                self._would(f"install managed block ({fix_id}): {path}")
            return updated != content
        if path not in files:
            created = not self.exists(path) or any(
                entry["files"].get(path, {}).get("created", False) for entry in manifest.values())
            files[path] = {"kind": "block", "created": created,
                           "separator": separator if not span else ""}
            self._save(manifest)
        if updated != content:
            mode = self.path(path).stat().st_mode & 0o7777 if self.exists(path) else 0o644
            self._atomic(path, updated.encode(), mode)
            self.log(f"{fix_id}: installed block in {path}")
            self._save(manifest)
        return updated != content

    def remove_block(self, fix_id: str, path: str) -> bool:
        manifest = self._manifest()
        files = self._entry(manifest, fix_id)["files"]
        record = files.get(path)
        if not record or record["kind"] != "block":
            return False
        content = self._text_for_edit(path)
        span = self._span(fix_id, content)
        if self.dry_run:
            if span:
                self._would(f"remove managed block ({fix_id}): {path}")
            return bool(span)
        if span:
            start, end = span
            if record.get("separator") and start and content[start - 1] == "\n":
                start -= 1
            updated = content[:start] + content[end:]
            if record["created"] and not updated.strip():
                self.path(path).unlink(missing_ok=True)
            else:
                mode = self.path(path).stat().st_mode & 0o7777
                self._atomic(path, updated.encode(), mode)
            self.log(f"{fix_id}: removed block from {path}")
        del files[path]
        self._save(manifest)
        return bool(span)

    def owned_paths(self, fix_id: str) -> list[str]:
        return sorted(self._manifest().get(fix_id, {}).get("files", {}))

    def note(self, fix_id: str, key: str, value: object) -> None:
        json.dumps(value)
        if self.dry_run:
            self._would(f"record state for {fix_id}: {key}")
            return
        manifest = self._manifest()
        self._entry(manifest, fix_id)["notes"][key] = value
        self._save(manifest)

    def notes(self, fix_id: str) -> dict:
        return copy.deepcopy(self._manifest().get(fix_id, {}).get("notes", {}))

    def clear_notes(self, fix_id: str) -> None:
        if self.dry_run:
            return
        manifest = self._manifest()
        self._entry(manifest, fix_id)["notes"] = {}
        self._save(manifest)

    def makedirs(self, path: str, mode: int = 0o755,
                 owner: tuple[int, int] | None = None) -> None:
        if self.dry_run:
            self._would(f"create directory: {path}")
            return
        self.path(path).mkdir(parents=True, exist_ok=True, mode=mode)
        self.path(path).chmod(mode)
        if owner is not None:
            self.chown(path, *owner)
        self.log(f"created directory: {path}")

    def chown(self, path: str, uid: int, gid: int) -> None:
        if self.dry_run:
            self._would(f"change owner: {path} to {uid}:{gid}")
            return
        os.chown(self.path(path), uid, gid)
        self.log(f"changed owner: {path}")

    def write_file(self, path: str, content: str | bytes, mode: int,
                   owner: tuple[int, int] | None = None) -> None:
        """Atomically write unmanaged data without recording it in the manifest."""
        if self.dry_run:
            self._would(f"write file: {path}")
            return
        self._atomic(path, self._bytes(content), mode)
        if owner is not None:
            self.chown(path, *owner)
        self.log(f"wrote file: {path}")

    def create_tar(self, src_dir: str, arcname: str, dst: str, mode: int,
                   owner: tuple[int, int] | None = None) -> None:
        """Atomically archive a directory without recording it in the manifest."""
        if self.dry_run:
            self._would(f"create archive: {src_dir} to {dst}")
            return
        source, target = self.path(src_dir), self.path(dst)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".parrot-mbp2017-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                with tarfile.open(fileobj=stream, mode="w") as tar:
                    tar.add(source, arcname=arcname)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
            target.chmod(mode)
            if owner is not None:
                self.chown(dst, *owner)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        self.log(f"created archive: {src_dir} to {dst}")

    def extract_tar(self, src: str, dst: str) -> None:
        if self.dry_run:
            self._would(f"extract archive: {src} to {dst}")
            return
        with tarfile.open(self.path(src), "r") as tar:
            for member in tar.getmembers():
                if member.issym() or member.islnk():
                    raise ValueError(f"{src} contains a link ({member.name}). Refusing to extract it.")
                member_path = PurePosixPath(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError(f"{src} contains an unsafe path ({member.name}). Refusing to extract it.")
            if hasattr(tarfile, "data_filter"):
                tar.extractall(self.path(dst), filter="data")
            else:
                tar.extractall(self.path(dst))

    def remove_tree(self, path: str) -> None:
        if self.dry_run:
            self._would(f"remove directory: {path}")
            return
        if self.exists(path):
            shutil.rmtree(self.path(path))
            self.log(f"removed directory: {path}")

    def copy_file(self, src: str, dst: str, mode: int,
                  owner: tuple[int, int] | None = None) -> None:
        if self.dry_run:
            self._would(f"copy file: {src} to {dst}")
            return
        self._atomic(dst, self.read_bytes(src), mode)
        if owner is not None:
            self.chown(dst, *owner)
        self.log(f"copied {src} to {dst}")

    def log(self, message: str) -> None:
        if self.dry_run:
            return
        try:
            target = self.path("/var/log/parrot-mbp2017.log")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as stream:
                stream.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")
        except Exception:
            pass  # Logging must never turn a successful fix into a failure.

    def package_versions(self, names: list[str]) -> dict[str, str]:
        if not names:
            return {}
        result = self.run(["dpkg-query", "-W", "-f=${Package}\t${Version}\t${db:Status-Abbrev}\n",
                           *names], check=False)
        versions = {}
        for line in result.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) == 3 and fields[2][:2] in ("ii", "hi"):
                versions[fields[0]] = fields[1]
        return versions
