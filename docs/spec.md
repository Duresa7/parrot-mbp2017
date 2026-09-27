# parrot-mbp2017 specification

## Goal

`parrot-mbp2017` is a command-line tool that makes Parrot OS 7 work properly on
2016-2017 Touch Bar MacBook Pros. It detects the hardware, shows the owner which
fixes their Mac needs, applies only those, and can undo each one. The audience
is owners of these Macs who are not Linux hardware experts, so every step says
what it does and why before doing it.

Tested machine: MacBookPro14,3 (15-inch, 2017) on Parrot OS 7.3 "echo"
(Debian 13 "trixie" base), kernel 7.0.9+parrot7-amd64, systemd 257, Python
3.13, KDE Plasma 6.3 on Wayland.

## Non-goals

- SSH, accounts, or any personal configuration.
- Router or network-specific settings. Locking Wi-Fi to 2.4 GHz was tried and
  reverted: routers that steer laptops to 5 GHz reject it and NetworkManager
  then gives up. The tool must not offer it.
- Bluetooth patch firmware, Thunderbolt resume errors, disabling unrelated
  Parrot services. These are documented as known issues only.
- Distributions that are not Parrot 7 or another Debian 13 based system. The
  tool warns on them and continues only after confirmation.

## Constraints

- Python 3.11+ standard library only. No pip dependencies. Runs from a git
  checkout as `sudo ./parrot-mbp2017`.
- Every read and write goes through the `System` module (`mbp2017/system.py`):
  paths are resolved under a root directory, commands go through an injectable
  runner, and `--dry-run` prints actions instead of performing them. This makes
  the whole tool testable against a scratch directory.
- Idempotent: running a fix that is already applied changes nothing.
- Reversible: `remove <fix>` restores the previous state. A file the tool
  replaces is backed up under `/var/lib/parrot-mbp2017/replaced/` and put back
  on removal. A file that already had identical content before the tool ran is
  left in place on removal.
- The manifest `/var/lib/parrot-mbp2017/manifest.json` records every file,
  managed block, package hold and other change per fix.
- T1 backups contain the Mac's serial number and T1 identity. They are private:
  directory mode 0700, files 0600, never printed.
- Network access only through apt, `git clone` of pinned upstream sources, and
  the package build (cargo, curl with sha256 checks).
- Plain, friendly wording in all output. No jargon without a short explanation.

## Hardware detection

| DMI `product_name` | Mac | T1 and Touch Bar | Graphics | Status |
| --- | --- | --- | --- | --- |
| MacBookPro13,1 | 13-inch, 2016, two Thunderbolt 3 ports | no | Intel | untested |
| MacBookPro13,2 | 13-inch, 2016, four Thunderbolt 3 ports | yes | Intel | untested |
| MacBookPro13,3 | 15-inch, 2016 | yes | Intel + AMD Radeon Pro 450/455/460 | untested |
| MacBookPro14,1 | 13-inch, 2017, two Thunderbolt 3 ports | no | Intel | untested |
| MacBookPro14,2 | 13-inch, 2017, four Thunderbolt 3 ports | yes | Intel | untested |
| MacBookPro14,3 | 15-inch, 2017 | yes | Intel + AMD Radeon Pro 555/560 | tested |

`sys_vendor` must be `Apple Inc.` and the model must be in the table, otherwise
the tool refuses to change anything unless `--force` is given (exit code 3).
Untested models get a warning. Fixes are gated on detected hardware, not on the
model name, so a fix only runs when its hardware is actually present.

Sources (all read through `System`):

| Fact | Source |
| --- | --- |
| Vendor, model | `/sys/class/dmi/id/sys_vendor`, `/sys/class/dmi/id/product_name` |
| AMD discrete GPU | `/sys/bus/pci/devices/*/vendor` = `0x1002` and `class` starting `0x0300` |
| Broadcom BCM43602 Wi-Fi | `/sys/bus/pci/devices/*`: vendor `0x14e4`, device `0x43ba`; bound driver = basename of the `driver` symlink |
| Cirrus Logic CS8409 sound | `/proc/asound/card*/codec#*` contains `Vendor Id: 0x10138409` |
| T1 | `/sys/bus/usb/devices/*/idVendor` `05ac` with `idProduct` `8600` (T1 running) or `1281` (T1 in recovery mode: its firmware is missing from the EFI partition). `bConfigurationValue` 2 means t1bridge mode, 1 means the firmware Touch Bar mode |
| Apple SPI keyboard and trackpad | `/sys/class/input/input*/name` is `Apple SPI Keyboard` / `Apple SPI Touchpad` |
| EFI system partition | vfat mount in `/proc/mounts` at `/boot/efi` or `/efi` |
| T1 data | `<ESP>/EFI/APPLE/EMBEDDEDOS/FDRData` (with `combined.memboot`, `version.plist`). The ESP is usually readable by root only; report "unknown, run with sudo" instead of failing |
| Distribution | `/etc/os-release` `ID`, `PRETTY_NAME`, `VERSION_CODENAME`, `ID_LIKE`. Parrot 7 is `ID=parrot`, `VERSION_CODENAME=echo`. Backports suite is `<codename>-backports` |
| Kernel | running release from `os.uname()`; installed kernels are `/lib/modules/*`; headers present when `/lib/modules/<kver>/build` exists |
| KDE Plasma | `/usr/bin/plasmashell` exists |
| Packages | `dpkg-query -W -f='${Package}\t${Version}\t${db:Status-Abbrev}\n' <names>` |
| DKMS | `dkms status` |

## Fixes

Fixes run in this order. Each has an id, a title, a one-line description, a
default (part of the recommended setup or opt-in), a hardware gate, a status
check, install and remove steps, and what the user must do afterwards.

Status values: `done`, `partly done`, `not set up`, `not needed` (hardware
absent; show the reason), `blocked` (needs another fix first; show which).

Every file the tool installs starts with a comment naming the tool and the fix,
for example `# Installed by parrot-mbp2017 (wifi).`

### t1-backup: back up the T1 firmware and Touch ID data

- Default: on. Gate: T1 present and T1 data present on the ESP.
- Why: Touch ID needs this Mac's T1 data in `EFI/APPLE` on the EFI partition.
  Reinstalling Linux often erases it, and recreating it takes an online
  recovery through Apple's servers.
- Install: archive the ESP's `EFI/APPLE` directory to
  `<backup dir>/EFI-APPLE-<YYYY-MM-DD>.tar` and write `SHA256SUMS` for every file
  in `EFI/APPLE/EMBEDDEDOS`. Default backup dir:
  `~<invoking user>/parrot-mbp2017-t1-backup/`, owned by the invoking user,
  mode 0700, files 0600, so they can copy it to a USB stick. Option
  `--backup-dir DIR`. Tell the user to keep a copy off the Mac and never share it.
- Status: `done` when the backup dir holds a backup whose `SHA256SUMS` match
  the current ESP files.
- Remove: never deletes backups. Prints where they are.
- `restore-t1 --from DIR` command: only when the ESP has no
  `EFI/APPLE/EMBEDDEDOS`. Verify the backup's checksums, then copy
  `EMBEDDEDOS` back into the ESP. Never overwrite existing T1 data. Tell the user
  to reboot; the T1 should then show up as `05ac:8600`. Documented as untested.
- `restore-t1 --online`: regenerate the T1 firmware through Apple's servers
  with [t1-revive](https://github.com/niconistal/t1-revive) at commit
  `c2062f3a09b2d278649d3ec48bbb7d15c8b55bcf`, for a T1 in recovery mode
  (`05ac:1281`) with no backup. This is the path that restored the tested Mac
  (about 2 minutes to build, 4 minutes to run). Steps:
  1. Refuse unless the T1 is at `05ac:1281`, the ESP has no
     `EFI/APPLE/EMBEDDEDOS`, the charger is connected
     (`/sys/class/power_supply/ADP1/online` is `1`) and the legacy
     `apple-ib-drv` Touch Bar driver is not installed (it can wedge the
     restore). Explain that Apple's servers (gs.apple.com, swcdn.apple.com)
     receive the T1's identity during signing, and ask for confirmation.
  2. Install build dependencies: `autoconf automake libtool pkgconf git patch
     libzip-dev libusb-1.0-0-dev libssl-dev zlib1g-dev libreadline-dev
     acpi-call-dkms python3`, and `libcurl4-openssl-dev` from
     `<codename>-backports` (from the main suite it conflicts with Parrot's
     backported libcurl). Then `modprobe acpi_call`.
  3. Stop `usbmuxd` if running (t1-revive runs its own).
  4. Clone to `/var/cache/parrot-mbp2017/t1-revive` owned by the invoking user,
     check out the pinned commit, and run `bash build.sh` as that user.
  5. `bin/t1-revive preflight` as root. Its only expected NO line on Parrot is
     the Arch package check; stop on any other NO line and show it.
  6. Back up the whole ESP to
     `/var/lib/parrot-mbp2017/esp-before-t1-revive-<date>.tar.gz`.
  7. Run `systemd-inhibit --what=sleep:idle:handle-lid-switch
     --who=parrot-mbp2017 --why="T1 firmware restore" bin/t1-revive
     --no-confirm regenerate` from the clone, output to
     `/var/log/parrot-mbp2017-t1-revive.log`, stage lines on screen.
  8. Verify the T1 is at `05ac:8600` and `EMBEDDEDOS/FDRData`,
     `combined.memboot` and `version.plist` exist. Tell the user to shut down
     fully and power on again (a warm reboot does not reset the T1), then run
     the `t1-backup` fix.
  Details and manual steps live in `docs/t1-recovery.md`.

### wifi: stable Wi-Fi on the Broadcom BCM43602

- Default: on. Gate: BCM43602 present.
- Why: other Broadcom drivers (`wl` from broadcom-sta, `b43`, `bcma` and
  others) fight `brcmfmac` for the chip, brcmfmac power saving causes drops,
  and NetworkManager stops retrying after 4 failed attempts, leaving the
  laptop offline after a short outage. The firmware also sometimes cannot
  sleep or wake in place: `brcmf_pcie_pm_enter_D3` times out, which cancels
  every later suspend, or the chip comes back unresponsive.
- Files:
  - `/etc/modprobe.d/parrot-mbp2017-broadcom.conf`: `blacklist` lines for
    `wl b43 b43legacy b44 bcma brcm80211 brcmsmac ssb`.
  - `/etc/NetworkManager/conf.d/parrot-mbp2017-wifi.conf`:

    ```ini
    [main]
    autoconnect-retries-default=0

    [connection-parrot-mbp2017-brcmfmac]
    match-device=driver:brcmfmac
    wifi.powersave=2
    ```

    `autoconnect-retries-default=0` means retry forever for connections left at
    the default; `wifi.powersave=2` turns power saving off.
  - `/usr/lib/systemd/system-sleep/parrot-mbp2017-wifi` (mode 0755), from
    `mbp2017/data/parrot-mbp2017-wifi-sleep`: on `pre`, if `brcmfmac` is
    loaded, `modprobe -r brcmfmac_wcc brcmfmac` and leave a marker in `/run`;
    on `post`, reload `brcmfmac` only if the marker is there. A failed unload
    must not stop the suspend.
- Also: purge `broadcom-sta-dkms` if installed (after confirmation). Install
  `firmware-brcm80211` if `/lib/firmware/brcm/brcmfmac43602-pcie.bin` is missing.
- After writing: `update-initramfs -u`, `nmcli general reload conf`. Reboot if
  `wl` is currently loaded.
- Remove: delete the three files, `update-initramfs -u`, reload NetworkManager.

### sleep: working suspend on the 15-inch models

- Default: on. Gates per item: AMD GPU present for the first two files; T1
  present for the third.
- Why: amdgpu does not resume from S3 ("deep") sleep on these Macs; the screen
  stays black. s2idle works, but only if PCIe devices stay out of D3cold and
  PCIe bridges stay awake. The T1 also wakes the Mac straight back up.
- Files:
  - `/etc/systemd/sleep.conf.d/parrot-mbp2017.conf`: `[Sleep]` with
    `MemorySleepMode=s2idle`. Treated as already satisfied when
    `/proc/cmdline` contains `mem_sleep_default=s2idle`.
  - `/etc/udev/rules.d/90-parrot-mbp2017-pci-pm.rules`:

    ```
    ACTION=="add|bind", SUBSYSTEM=="pci", ATTR{d3cold_allowed}="0"
    ACTION=="add|bind", SUBSYSTEM=="pci", DRIVER=="pcieport", ATTR{power/control}="on"
    ```

  - `/etc/udev/rules.d/99-zz-parrot-mbp2017-t1-nowake.rules`:

    ```
    ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="05ac", ATTR{idProduct}=="8600", ATTR{power/wakeup}="disabled"
    ```

- After writing: `udevadm control --reload`. Takes full effect after a reboot.
- Known issues to print: t1bridge says the T1 does not survive sleep yet, so the
  Touch Bar or Touch ID may need a reboot after waking; Thunderbolt logs errors
  on resume.

### audio: internal speakers

- Default: on. Gate: CS8409 codec present.
- Why: the mainline `snd-hda-codec-cs8409` driver does not drive the MacBook
  Pro speaker amplifiers, so the speakers are silent.
- Install: the out-of-tree driver
  [davidjo/snd_hda_macbookpro](https://github.com/davidjo/snd_hda_macbookpro)
  at commit `89b22ff90b86468b186706861dd18663562defa7`, installed through DKMS
  (name `snd_hda_macbookpro/0.1`) so it rebuilds for new kernels.
  1. Install `build-essential dkms git patch wget` and
     `linux-headers-<running kernel>` if missing.
  2. `git clone https://github.com/davidjo/snd_hda_macbookpro.git
     /usr/local/src/snd_hda_macbookpro` and check out the pinned commit (verify
     `git rev-parse HEAD`).
  3. `./install.cirrus.driver.sh -i` from that directory. It runs `dkms.sh`,
     which symlinks `/usr/src/snd_hda_macbookpro-0.1` to the clone and runs
     `dkms install`. The DKMS pre-build step downloads the matching kernel
     source from cdn.kernel.org, so every kernel update needs internet to
     rebuild the module. Took about 20 s on the tested Mac.
  4. Speakers work after a reboot.
- Status: `done` when `dkms status snd_hda_macbookpro` reports `installed` for
  the running kernel.
- Health: warn for every installed kernel with headers where the module is not
  installed.
- Remove: `./install.cirrus.driver.sh -r` (runs `dkms remove`, which restores
  the stock module, and deletes the `/usr/src` symlink), then delete
  `/usr/local/src/snd_hda_macbookpro`.

### input: palm rejection and Touch Bar touches

- Default: on. Gates per item: Apple SPI keyboard present for palm rejection;
  T1 present for the Touch Bar rule.
- Why: the applespi driver reports vendor `0x0000` for the built-in keyboard,
  so libinput's `[Apple Internal Keyboard (SPI)]` quirk (which matches vendor
  `0x05AC`) never applies. libinput then treats the keyboard as external and
  never turns on disable-while-typing, so resting palms move the cursor. In
  t1bridge mode the T1 also exposes the Touch Bar digitizer as an evdev
  touchpad, which libinput would use as a second trackpad.
- Palm rejection: libinput reads a single local file,
  `/etc/libinput/local-overrides.quirks`, so the tool adds a managed block
  (marker lines `# >>> parrot-mbp2017 input >>>` and
  `# <<< parrot-mbp2017 input <<<`) and never replaces other content:

  ```ini
  [parrot-mbp2017 Apple SPI Keyboard]
  MatchUdevType=keyboard
  MatchBus=spi
  MatchName=Apple SPI Keyboard
  AttrKeyboardIntegration=internal
  ```

  Removal deletes the block, and the file if the tool created it and it is now
  empty.
- `/etc/udev/rules.d/90-parrot-mbp2017-touchbar-not-pointer.rules`:

  ```
  ACTION=="add|change", SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_TOUCHPAD}=="1", ATTRS{idVendor}=="05ac", ATTRS{idProduct}=="8600", ENV{LIBINPUT_IGNORE_DEVICE}="1"
  ```

- After writing: `udevadm control --reload`, then
  `udevadm trigger --action=change --subsystem-match=input`. The user logs out
  and back in.
- Verified on the tested Mac with `libinput debug-events --verbose`, which
  prints `palm: dwt activated with Apple SPI Touchpad<->Apple SPI Keyboard`
  and `event6: device is ignored` for the T1 digitizer.

### t1bridge: Touch Bar, Touch ID, camera and ambient light sensor

- Default: on. Gate: T1 present. Warn when the T1 is in recovery mode (Touch Bar
  will not work until the T1 firmware is restored) or T1 data is missing (Touch
  ID will not work).
- Upstream: [standardagents/t1bridge](https://github.com/standardagents/t1bridge)
  tag `v0.1.12`, commit `81cbdf81026a16e02f0bea74735c6b029a8ffae2`. Upstream
  ships Arch packages only; this repo builds Debian packages from the pinned
  source.
- Packages: `t1bridge`, `t1bridge-dkms`, `libfprint-2-2`, `fprintd`,
  `libpam-fprintd` (the build also produces `libfprint-2-dev` and
  `gir1.2-fprint-2.0`). Packaging lives in `packaging/t1bridge/`.
- Install steps:
  1. Remove the legacy firmware-mode Touch Bar driver if present: DKMS module
     `apple-ib-drv`, `/usr/src/apple-ib-drv-*`,
     `/etc/modprobe.d/apple-touchbar.conf`,
     `/etc/modules-load.d/apple-touchbar.conf`,
     `/etc/udev/rules.d/99-ibridge.rules`. It pins the T1 to USB configuration
     1, which conflicts with t1bridge.
  2. Get the packages: `--debs DIR` if given; otherwise a cached build in
     `/var/cache/parrot-mbp2017/t1bridge-<version>/`; otherwise build them with
     `packaging/t1bridge/build-debs.sh`: in Docker when the daemon is
     available (about 5 minutes on 6 cores, nothing installed on the host),
     else natively with `--native` (installs the build dependencies with apt;
     Parrot ships its `deb-src` lines commented out, so the native build adds
     `/etc/apt/sources.list.d/parrot-mbp2017-src.list` for the build and
     removes it afterwards). Build output goes to
     `/var/log/parrot-mbp2017-build.log`; the user sees only stage lines and a
     time estimate. Check the package set is complete before removing
     anything in step 1.
  3. `apt-get install -y --allow-downgrades` the five packages using absolute
     paths, then `apt-mark hold libfprint-2-2 fprintd libpam-fprintd` so an
     update cannot split the matched pair.
  4. Add the invoking user to the `t1bridge` group.
  5. usbmuxd: if `/usr/lib/udev/rules.d/39-usbmuxd.rules` contains
     `|5ac/8600/*`, write `/etc/udev/rules.d/39-usbmuxd.rules` as that file with
     `|5ac/8600/*` removed. The stock rule writes `bConfigurationValue=0` to the
     T1 and deconfigures it. Record the source file's sha256 so `status` can
     warn when the package rule changes.
  6. Print next steps: reboot; `sudo t1bridge status` (every row `ready`);
     `sudo systemctl start t1bridge-import.service`; enroll as the normal user
     without sudo: `fprintd-enroll -f right-index-finger`, then
     `fprintd-verify -f right-index-finger`. Enrolling with sudo fails.
- Status: `done` when both t1bridge packages are installed at the pinned
  version, the holds are in place, the usbmuxd override exists when needed and
  the user is in the group.
- Remove: remove `t1bridge` and `t1bridge-dkms`, unhold, reinstall the
  distribution's `libfprint-2-2`, `fprintd`, `libpam-fprintd` with
  `--allow-downgrades`, remove the `-dev`/gir packages if they are the t1bridge
  builds, delete the usbmuxd override. Keep `/var/lib/t1bridge`.

### desktop: Touch Bar volume and media keys, brightness popups

- Default: on. Gate: T1 present. `blocked` until the t1bridge fix is done (or is
  selected in the same run).
- Why: t1bridge ships no desktop integration. Without a "desktop provider" the
  Touch Bar hides its volume, mute and media buttons, and brightness changes
  show no popup because t1bridge sets brightness through logind instead of
  sending key presses, which also leaves Plasma's brightness slider out of sync.
- Files:
  - `/usr/local/lib/parrot-mbp2017/desktop-provider` (mode 0755), from
    `mbp2017/data/desktop-provider`. Implements t1bridge's desktop provider v1
    contract with `wpctl` (volume), MPRIS over `busctl` (media) and Plasma's
    PowerDevil and OSD D-Bus services (popups, slider sync).
  - `/etc/systemd/user/t1-touchbar.service.d/parrot-mbp2017-desktop-provider.conf`:
    `[Service]` with
    `Environment=T1BRIDGE_DESKTOP_PROVIDER=/usr/local/lib/parrot-mbp2017/desktop-provider`.
- After writing: best effort `systemctl --user -M <user>@ daemon-reload` and
  `restart t1-touchbar.service` for the invoking user; otherwise tell them to
  log out and back in.

### fingerprint-login: Touch ID for sudo and the lock screen

- Default: off (opt-in). Gate: `libpam-fprintd` installed.
- Install: explain that the password keeps working, suggest keeping a root
  terminal open and testing `sudo` in a new one, warn if `fprintd-list <user>`
  shows no enrolled finger, then `pam-auth-update --enable fprintd`.
- Status: `done` when `/etc/pam.d/common-auth` contains `pam_fprintd.so`.
- Remove: `pam-auth-update --disable fprintd`.

## Command line

| Command | Behaviour |
| --- | --- |
| `parrot-mbp2017` | same as `setup` |
| `setup` | Wizard: banner, hardware summary, fix table with status, a recommended selection (applicable, default on, not done), toggle by number, confirm, apply in order, then a summary with next steps and whether a reboot or log-out is needed |
| `status` | Hardware summary, fix table and health checks. Works without root and says what needs sudo |
| `detect` | Hardware summary only |
| `install FIX...` | Apply the named fixes |
| `remove FIX...` | Undo the named fixes |
| `restore-t1 --from DIR` | Put a T1 backup back on the EFI partition |
| `restore-t1 --online` | Regenerate lost T1 firmware with t1-revive |
| `list` | Every fix with its description and why it exists |

Options: `--yes` (accept defaults, no prompts), `--dry-run`, `--only IDS` and
`--skip IDS` (setup), `--debs DIR`, `--backup-dir DIR`, `--json` (status and
detect), `--force` (unsupported hardware), `--no-color`, `--version`, and a
hidden `--root DIR` for tests. `setup`, `install`, `remove` and `restore-t1`
need root unless `--dry-run` is given. Colour only when stdout is a terminal
and `NO_COLOR` is unset.

Exit codes: 0 success, 1 a fix failed, 2 usage error, 3 unsupported hardware.

### Health checks in `status`

- For every installed kernel: kernel headers present, and `t1bridge-dkms` and
  the speaker module built. A kernel without headers loses the Touch Bar and
  speakers when booted.
- `t1bridge status` rows that are not `ready` (when root and installed).
- Package holds present when t1bridge is installed.
- Invoking user in the `t1bridge` group.
- usbmuxd override out of date with the package rule.
- ufw active: remind that t1bridge needs inbound IPv6 TCP 61500 on the private
  T1 link only.

## Logging

Every command run and every file change is appended to
`/var/log/parrot-mbp2017.log` with a timestamp.

## Quality

- Tests: `python3 -m unittest discover -s tests` from the repo root passes with
  no network and no root, using fixture sysfs trees in a temp dir and a fake
  command runner.
- Shell scripts pass `bash -n` and `shellcheck`.
- Docs: `README.md` (what it does, supported Macs, quick start, what each fix
  changes, undo, troubleshooting), `docs/fixes.md`, `docs/t1-recovery.md`,
  `docs/hardware.md`.
