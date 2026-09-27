"""Shared queries for DKMS module build status."""

from .system import System


def entries(system: System, module: str, *, require_success: bool = False) -> list[dict[str, str]]:
    result = system.run(["dkms", "status", module], check=False)
    if require_success and not result.ok:
        return []
    found = []
    for line in result.stdout.splitlines():
        head, sep, status = line.partition(":")
        if not sep:
            continue
        fields = [field.strip() for field in head.split(",")]
        if len(fields) < 2 or fields[0].split("/")[0] != module:
            continue
        found.append({"kernel": fields[1], "status": status.strip()})
    return found


def installed_kernels(system: System, module: str) -> set[str]:
    return {entry["kernel"] for entry in entries(system, module, require_success=True)
            if "installed" in entry["status"]}
