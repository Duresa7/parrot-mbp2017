# Hardware

This page lists which 2016-2017 Touch Bar MacBook Pros `parrot-mbp2017`
supports, what it detects, where each fact comes from, and what is known to
still be rough.

## Supported models

`parrot-mbp2017` reads the DMI product name and only changes anything on the
models below. Any other machine, or a non-Apple `sys_vendor`, is refused
unless you pass `--force`.

| DMI `product_name` | Mac | T1, Touch Bar | Graphics | Status |
| --- | --- | --- | --- | --- |
| MacBookPro13,1 | 13-inch, 2016, two Thunderbolt 3 ports | no | Intel | untested |
| MacBookPro13,2 | 13-inch, 2016, four Thunderbolt 3 ports | yes | Intel | untested |
| MacBookPro13,3 | 15-inch, 2016 | yes | Intel + AMD Radeon Pro 450/455/460 | untested |
| MacBookPro14,1 | 13-inch, 2017, two Thunderbolt 3 ports | no | Intel | untested |
| MacBookPro14,2 | 13-inch, 2017, four Thunderbolt 3 ports | yes | Intel | untested |
| MacBookPro14,3 | 15-inch, 2017 | yes | Intel + AMD Radeon Pro 555/560 | tested |

"Untested" models are still in scope: the detection and fixes below are
written from the public hardware differences between these models, but only
MacBookPro14,3 has been run through the whole tool on real hardware. Fixes are
gated on hardware actually present (an AMD GPU, a T1, a particular Wi-Fi
chip), not on the model name, so a two-port 13-inch model with no T1 simply
gets fewer fixes offered — nothing needs a model check beyond confirming
you're on a supported Mac.

Check your own model with:

```
cat /sys/class/dmi/id/product_name
```

## What is detected, and from where

Every fact below is read through `mbp2017/system.py`, so it works the same
against a real machine or a test fixture, and requires no network access.

| Fact | Source |
| --- | --- |
| Vendor, model | `/sys/class/dmi/id/sys_vendor`, `/sys/class/dmi/id/product_name` |
| AMD discrete GPU | A PCI device under `/sys/bus/pci/devices/*` with vendor `0x1002` and a class starting `0x0300` (display controller) |
| Broadcom BCM43602 Wi-Fi | A PCI device with vendor `0x14e4`, device `0x43ba`; the bound driver is read from the `driver` symlink |
| Cirrus Logic CS8409 sound codec | `/proc/asound/card*/codec#*` contains `Vendor Id: 0x10138409` |
| T1 chip and its state | A USB device under `/sys/bus/usb/devices/*` with `idVendor` `05ac`: `idProduct` `8600` means the T1 is running, `1281` means it's in recovery mode with no firmware loaded. `bConfigurationValue` `2` means it's in t1bridge mode, `1` means the legacy firmware Touch Bar mode |
| Apple SPI keyboard and trackpad | `/sys/class/input/input*/name` equal to `Apple SPI Keyboard` or `Apple SPI Touchpad` |
| EFI system partition | A `vfat` mount in `/proc/mounts` at `/boot/efi` or `/efi` |
| T1 firmware and Touch ID data | `<EFI partition>/EFI/APPLE/EMBEDDEDOS/FDRData`, plus `combined.memboot` and `version.plist` alongside it. This directory is usually readable by root only; without sudo the tool reports "unknown, run with sudo" rather than assuming it's missing |
| Distribution | `/etc/os-release`: `ID`, `PRETTY_NAME`, `VERSION_CODENAME`, `ID_LIKE`. Parrot 7 reports `ID=parrot`, `VERSION_CODENAME=echo` |
| Kernel | The running kernel from `uname -r`; every installed kernel from `/lib/modules/*`; headers present when `/lib/modules/<kernel>/build` exists |
| KDE Plasma | `/usr/bin/plasmashell` exists |
| Installed packages | `dpkg-query -W` for the package names each fix cares about |
| DKMS module status | `dkms status` |

Run `sudo ./parrot-mbp2017 status` (or `./parrot-mbp2017 detect` without root,
though the T1 data row then reads "unknown, run with sudo") to see all of this
for your own Mac.

## Tested configuration

The fixes were developed and checked on:

- **Model:** MacBookPro14,3 (15-inch, 2017, Radeon Pro 560)
- **Distribution:** Parrot OS 7.3 "echo" (Debian 13 "trixie" base)
- **Kernel:** 7.0.9+parrot7-amd64
- **Init:** systemd 257
- **Python:** 3.13
- **Desktop:** KDE Plasma 6.3 on Wayland

On that Mac the tool itself ran `setup --yes`, which backed up the T1 data
and applied the wifi, sleep, input, t1bridge (usbmuxd rule only, since the
packages were already installed from the same build) and desktop fixes.
`status` then reported every fix done, a second run changed nothing, and
`remove input` followed by `install input` switched palm rejection off and
on again. Not yet run on hardware through the tool: `restore-t1`,
`fingerprint-login`, `remove t1bridge`, and a fresh t1bridge build and
install on a Mac without it.

Other models in the table use the same detection logic and the same fixes,
gated on the same hardware signals, but have not been run on real hardware
yet. If you try one, `sudo ./parrot-mbp2017 status --json` output is useful to
include in a bug report.

## Known hardware limitations

These are not bugs in the tool; they're documented so you know what to expect
and don't spend time chasing them:

- **Bluetooth patch firmware is missing, but Bluetooth still works.** The
  in-tree `btusb` driver runs the chip without Apple's patch firmware. Some
  extra low-power features that the patch would unlock are not tested.
- **Thunderbolt logs errors on resume from sleep.** These are warnings in the
  kernel log after waking up, not a functional failure — Thunderbolt devices
  still work. The `sleep` fix's summary output mentions this after it runs.
- **s2idle sleep draws about 7-13 W** according to other owners of the
  15-inch 2017 model, far more than macOS's deep sleep. Expect faster battery
  drain while the lid is closed. Not yet measured on the tested Mac.
- **The lid may not wake the Mac from s2idle; a key press does**, as other
  owners report. Not yet checked on the tested Mac.
- **The Touch Bar stays dark after deep (S3) sleep**, upstream reports
  ([t1bridge#18](https://github.com/standardagents/t1bridge/issues/18)). The
  `t1-wake` fix brings the Touch Bar and Touch ID back after the light
  (s2idle) sleep the `sleep` fix sets on 15-inch models. 13-inch models keep
  their default sleep mode and are untested. See
  [fixes.md](fixes.md#t1-wake-touch-bar-and-touch-id-after-sleep).
