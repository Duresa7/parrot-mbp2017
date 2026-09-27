"""Offline fixtures shared by all fix tests.

make_mac accepts hardware field names plus amd, bcm, cs8409, t1, kver,
headers (bool or kver->bool), kernels, esp, t1_data, and user.
FakeRunner scripts map argv tuples to Results (or lists of Results).
Longest matching prefix wins; calls records plain argv lists and options
records the corresponding runner options.
"""

import os
from pathlib import Path

from mbp2017.system import Result

KERNEL = "7.0.9+parrot7-amd64"
# Fixtures chown files to alice for real, so she must be the user running the
# tests (root can chown to anyone, so it keeps a separate non-root uid).
ALICE_UID = os.getuid() if os.getuid() != 0 else 1000
ALICE_GID = os.getgid() if os.getuid() != 0 else 1000


def make_mac(tmpdir: str, **overrides: object) -> str:
    root = Path(tmpdir)
    config = dict(vendor="Apple Inc.", model="MacBookPro14,3", amd=True, bcm=True,
                  cs8409=True, t1="running", t1_config=2, spi_keyboard=True,
                  spi_touchpad=True, esp="/boot/efi", t1_data=True, distro_id="parrot",
                  distro_name="Parrot OS 7.3", distro_codename="echo", distro_like="debian",
                  kver=KERNEL, plasma=True)
    aliases = {"amd_gpu": "amd", "wifi_bcm43602": "bcm", "audio_cs8409": "cs8409",
               "t1_state": "t1", "kernel": "kver"}
    config.update({aliases.get(key, key): value for key, value in overrides.items()})

    def write(path: str, content: str = "") -> Path:
        target = root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    write("/sys/class/dmi/id/sys_vendor", str(config["vendor"]) + "\n")
    write("/sys/class/dmi/id/product_name", str(config["model"]) + "\n")
    devices = [("0000:00:02.0", "8086", "591b")]
    if config["amd"]:
        devices.append(("0000:01:00.0", "1002", "67ef"))
    if config["bcm"]:
        devices.append(("0000:03:00.0", "14e4", "43ba"))
    for name, vendor, device in devices:
        base = f"/sys/bus/pci/devices/{name}"
        write(base + "/vendor", f"0x{vendor}\n")
        write(base + "/device", f"0x{device}\n")
        write(base + "/class", "0x028000\n" if vendor == "14e4" else "0x030000\n")
        if vendor == "14e4":
            driver = config.get("wifi_driver", "brcmfmac")
            if driver:
                target = root / "sys/bus/pci/drivers" / str(driver)
                target.mkdir(parents=True, exist_ok=True)
                (root / base.lstrip("/") / "driver").symlink_to("../../drivers/" + str(driver))
    if config["cs8409"]:
        write("/proc/asound/card0/codec#0", "Codec: Cirrus Logic CS8409\nVendor Id: 0x10138409\n")
    if config["t1"]:
        base = "/sys/bus/usb/devices/1-3"
        write(base + "/idVendor", "05ac\n")
        write(base + "/idProduct", "1281\n" if config["t1"] == "recovery" else "8600\n")
        write(base + "/bConfigurationValue", str(config["t1_config"]) + "\n")
    for index, kind in enumerate(("keyboard", "touchpad")):
        if config["spi_" + kind]:
            write(f"/sys/class/input/input{index}/name", f"Apple SPI {kind.title()}\n")
    esp = config["esp"]
    write("/proc/mounts", f"/dev/nvme0n1p1 {esp} vfat rw 0 0\n" if esp else "")
    if esp:
        (root / str(esp).lstrip("/")).mkdir(parents=True, exist_ok=True)
        if config["t1_data"]:
            embedded = str(esp) + "/EFI/APPLE/EMBEDDEDOS"
            (root / embedded.lstrip("/") / "FDRData").mkdir(parents=True)
            write(embedded + "/FDRData/fixture", "private fixture data\n")
            write(embedded + "/combined.memboot", "fixture firmware\n")
            write(embedded + "/version.plist", "fixture version\n")
    write("/etc/os-release", f'ID={config["distro_id"]}\nPRETTY_NAME="{config["distro_name"]}"\n'
          f'VERSION_CODENAME={config["distro_codename"]}\nID_LIKE="{config["distro_like"]}"\n')
    for kernel in config.get("kernels", [config["kver"]]):
        (root / "lib/modules" / kernel).mkdir(parents=True, exist_ok=True)
        headers = config.get("headers", True)
        if headers.get(kernel, False) if isinstance(headers, dict) else headers:
            (root / "lib/modules" / kernel / "build").mkdir()
    if config["plasma"]:
        write("/usr/bin/plasmashell").chmod(0o755)
    write("/etc/passwd", "root:x:0:0:root:/root:/bin/bash\n"
          f"alice:x:{ALICE_UID}:{ALICE_GID}:Alice:/home/alice:/bin/bash\n")
    return str(root)


class FakeRunner:
    def __init__(self, scripts: dict | None = None):
        self.scripts = dict(scripts or {})
        self.calls: list[list[str]] = []
        self.options: list[dict] = []

    def __call__(self, argv: list[str], opts: dict) -> Result:
        self.calls.append(list(argv))
        self.options.append(dict(opts))
        for prefix in sorted(self.scripts, key=len, reverse=True):
            if tuple(argv[:len(prefix)]) == tuple(prefix):
                scripted = self.scripts[prefix]
                if isinstance(scripted, list):
                    return scripted.pop(0) if scripted else Result(0)
                return scripted
        return Result(0)
