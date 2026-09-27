"""Hardware facts, read through the injectable system boundary."""

from dataclasses import asdict, dataclass, field
import shlex

from .system import System

MODELS = {
    "MacBookPro13,1": "13-inch, 2016, two Thunderbolt 3 ports",
    "MacBookPro13,2": "13-inch, 2016, four Thunderbolt 3 ports",
    "MacBookPro13,3": "15-inch, 2016",
    "MacBookPro14,1": "13-inch, 2017, two Thunderbolt 3 ports",
    "MacBookPro14,2": "13-inch, 2017, four Thunderbolt 3 ports",
    "MacBookPro14,3": "15-inch, 2017",
}


@dataclass
class Hardware:
    vendor: str = ""
    model: str = ""
    model_name: str = "Unknown model"
    known_model: bool = False
    tested: bool = False
    amd_gpu: bool = False
    wifi_bcm43602: bool = False
    wifi_driver: str | None = None
    audio_cs8409: bool = False
    t1_state: str | None = None
    t1_config: int | None = None
    spi_keyboard: bool = False
    spi_touchpad: bool = False
    esp: str | None = None
    t1_data: bool | None = None
    distro_id: str = ""
    distro_name: str = "Unknown system"
    distro_codename: str = ""
    distro_like: str = ""
    kernel: str = ""
    kernels: list[str] = field(default_factory=list)
    headers: dict[str, bool] = field(default_factory=dict)
    plasma: bool = False

    @property
    def t1(self) -> bool:
        return self.t1_state is not None

    @property
    def supported(self) -> bool:
        return self.vendor == "Apple Inc." and self.known_model

    @property
    def is_parrot(self) -> bool:
        return self.distro_id == "parrot"

    @property
    def debian13_based(self) -> bool:
        return self.distro_codename in ("echo", "trixie")


def probe(system: System) -> Hardware:
    def read(path: str) -> str:
        return (system.read_text(path, "") or "").strip()

    hw = Hardware(vendor=read("/sys/class/dmi/id/sys_vendor"),
                  model=read("/sys/class/dmi/id/product_name"))
    hw.known_model = hw.model in MODELS
    hw.model_name = MODELS.get(hw.model, hw.model or "Unknown model")
    hw.tested = hw.supported and hw.model == "MacBookPro14,3"
    for device in system.glob("/sys/bus/pci/devices/*"):
        vendor = read(device + "/vendor").lower()
        if vendor == "0x1002" and read(device + "/class").startswith("0x0300"):
            hw.amd_gpu = True
        if vendor == "0x14e4" and read(device + "/device").lower() == "0x43ba":
            hw.wifi_bcm43602 = True
            hw.wifi_driver = system.readlink_name(device + "/driver")
    hw.audio_cs8409 = any("vendor id: 0x10138409" in read(path).lower()
                          for path in system.glob("/proc/asound/card*/codec#*"))
    for device in system.glob("/sys/bus/usb/devices/*"):
        product = read(device + "/idProduct").lower()
        if read(device + "/idVendor").lower() == "05ac" and product in ("8600", "1281"):
            hw.t1_state = "running" if product == "8600" else "recovery"
            try:
                hw.t1_config = int(read(device + "/bConfigurationValue"))
            except ValueError:
                pass
            break
    names = {read(path) for path in system.glob("/sys/class/input/input*/name")}
    hw.spi_keyboard = "Apple SPI Keyboard" in names
    hw.spi_touchpad = "Apple SPI Touchpad" in names
    for line in read("/proc/mounts").splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[1] in ("/boot/efi", "/efi") and fields[2] == "vfat":
            hw.esp = fields[1]
            break
    # Non-root absence is not evidence of missing private firmware data.
    if hw.esp:
        try:
            present = all(system.exists(hw.esp + "/EFI/APPLE/EMBEDDEDOS/" + name)
                          for name in ("FDRData", "combined.memboot", "version.plist"))
            hw.t1_data = True if present else (False if system.is_root() else None)
        except PermissionError:
            hw.t1_data = None
    distro = {}
    for line in read("/etc/os-release").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        try:
            distro[key] = " ".join(shlex.split(value))
        except ValueError:
            continue
    hw.distro_id = distro.get("ID", "")
    hw.distro_name = distro.get("PRETTY_NAME", "Unknown system")
    hw.distro_codename = distro.get("VERSION_CODENAME", "")
    hw.distro_like = distro.get("ID_LIKE", "")
    hw.kernel = system.kernel_release()
    hw.kernels = [name for name in system.listdir("/lib/modules")
                  if system.is_dir("/lib/modules/" + name)]
    hw.headers = {name: system.exists(f"/lib/modules/{name}/build") for name in hw.kernels}
    hw.plasma = system.exists("/usr/bin/plasmashell")
    return hw


def summary_rows(hw: Hardware) -> list[tuple[str, str]]:
    return [
        ("Model", f"{hw.model} — {hw.model_name}"),
        ("System", f"{hw.distro_name}, kernel {hw.kernel}"),
        ("Graphics", "Intel + AMD Radeon Pro" if hw.amd_gpu else "No AMD graphics detected"),
        ("Wi-Fi", f"Broadcom BCM43602 ({hw.wifi_driver or 'no driver bound'})"
         if hw.wifi_bcm43602 else "BCM43602 not detected"),
        ("Sound", "Cirrus Logic CS8409" if hw.audio_cs8409 else "CS8409 not detected"),
        ("Touch Bar (T1)", (f"{hw.t1_state}, USB configuration {hw.t1_config}"
                           if hw.t1 else "not detected")),
        ("T1 data", "present" if hw.t1_data is True else
         "missing" if hw.t1_data is False else "unknown, run with sudo"),
        ("Keyboard", "Apple SPI Keyboard" if hw.spi_keyboard else "Apple SPI keyboard not detected"),
        ("Desktop", "KDE Plasma" if hw.plasma else "KDE Plasma not detected"),
    ]


def to_dict(hw: Hardware) -> dict:
    return asdict(hw) | {"t1": hw.t1, "supported": hw.supported,
                         "is_parrot": hw.is_parrot, "debian13_based": hw.debian13_based}
