# What each fix does

Every fix below can be applied on its own with `sudo ./parrot-mbp2017 install <id>`
and undone with `sudo ./parrot-mbp2017 remove <id>`. `sudo ./parrot-mbp2017 status`
shows whether a fix is done, partly done, not set up, not needed for your
hardware, or blocked on another fix. See [hardware.md](hardware.md) for how
each hardware fact is detected, and the main [README](../README.md) for a
plain-English overview and the full command reference.

The "How it was verified" notes describe what was checked on the tested
MacBookPro14,3 when the same changes were first made by hand, before they
were collected into this tool. Anything not checked there says so.

## t1-backup: back up the T1 firmware and Touch ID data

**The problem:** if the small folder the T1 chip needs ever goes missing from
the EFI partition, the Touch Bar goes blank and Touch ID stops working, and
the only way back is a backup or an online recovery through Apple's servers.

**The cause:** reinstalling Linux, repartitioning, or reformatting the EFI
partition can erase `EFI/APPLE` on it, which holds the T1's firmware and this
Mac's Touch ID enrollment identity. Nothing recreates it automatically.

**What changes:**

- Copies `EFI/APPLE` from the EFI partition into a dated archive:
  `<backup dir>/EFI-APPLE-<YYYY-MM-DD>.tar`, plus a `SHA256SUMS` file listing
  a checksum for every file under `EFI/APPLE/EMBEDDEDOS`.
- Default backup directory: `~<invoking user>/parrot-mbp2017-t1-backup/`,
  created with mode `0700` and files at `0600`, owned by the invoking user
  (override with `--backup-dir DIR`).
- No packages, no system files.

**Undo:** `remove t1-backup` never deletes anything — it only prints where
your backups are, since the whole point is to keep them around.

**Also provides two recovery commands**, documented in full in
[t1-recovery.md](t1-recovery.md):

- `restore-t1 --from DIR` copies a backup's `EFI/APPLE` back onto the EFI
  partition. It refuses to run if `EFI/APPLE/EMBEDDEDOS` already exists there,
  so it never overwrites working T1 data.
- `restore-t1 --online` regenerates lost T1 firmware through Apple's signing
  servers using [t1-revive](https://github.com/niconistal/t1-revive) (pinned
  commit `c2062f3a09b2d278649d3ec48bbb7d15c8b55bcf`), for a T1 stuck in
  recovery mode with no backup available. It installs a short list of build
  packages, clones and builds t1-revive, backs up the whole EFI partition
  first, then runs `t1-revive regenerate` under `systemd-inhibit` so sleep or
  a lid close can't interrupt it.

**How it was verified:** on the tested Mac, the same t1-revive steps, run by
hand, brought a T1 stuck in recovery mode (`05ac:1281`) back to running
(`05ac:8600`) with `EMBEDDEDOS/FDRData`, `combined.memboot` and
`version.plist` all present afterwards: about 2 minutes to build t1-revive
and 4 minutes to run it. The tool's `--online` wrapper around those steps
has not run on hardware yet. The
`--from DIR` backup-restore path has not been exercised on real hardware; a
backup made with `t1-backup` and its checksums are verified before any files
are written, but treat that path as unproven until confirmed.

## wifi: stable Wi-Fi on the Broadcom BCM43602

**The problem:** Wi-Fi drops out, especially on 5 GHz networks, and sometimes
does not reconnect on its own. After some sleeps the Mac also refuses to
sleep at all, or Wi-Fi stays dead after waking.

**The cause:** several possible drivers can claim this chip (`wl` from
broadcom-sta, `b43`, `bcma`, and others) and fight the in-tree `brcmfmac`
driver for it; `brcmfmac`'s power saving can drop the link; and
NetworkManager gives up retrying a connection after 4 failed attempts,
leaving the laptop offline until you intervene. The chip's firmware also
sometimes cannot sleep or wake in place
([kernel bug 196019](https://bugzilla.kernel.org/show_bug.cgi?id=196019)):
`brcmfmac` then times out entering its sleep state, which cancels the whole
suspend, or the chip comes back unresponsive.

**What changes:**

- `/etc/modprobe.d/parrot-mbp2017-broadcom.conf` blacklists `wl`, `b43`,
  `b43legacy`, `b44`, `bcma`, `brcm80211`, `brcmsmac` and `ssb`.
- `/etc/NetworkManager/conf.d/parrot-mbp2017-wifi.conf` sets
  `autoconnect-retries-default=0` (retry forever) and turns off power saving
  (`wifi.powersave=2`) for the `brcmfmac` device.
- Installs the `firmware-brcm80211` package if
  `/lib/firmware/brcm/brcmfmac43602-pcie.bin` is missing.
- Removes the `broadcom-sta-dkms` package if it's installed, after asking for
  confirmation, since it's the main source of driver conflicts.
- `/usr/lib/systemd/system-sleep/parrot-mbp2017-wifi` (mode `0755`) unloads
  `brcmfmac` before sleep and loads it again after waking, the same clean
  start the chip gets at boot. It only reloads a driver it unloaded itself;
  if unloading fails, the Mac sleeps with the driver loaded, as before.
- Runs `update-initramfs -u` and `nmcli general reload conf` afterwards so the
  running system picks up the change; a reboot is only needed if `wl` is
  currently loaded.

**Undo:** `remove wifi` deletes its three files, then re-runs the same
initramfs update and NetworkManager reload.

**Note:** the tool deliberately does not offer to lock Wi-Fi to the 2.4 GHz
band. That was tried during development and reverted — routers that steer
clients onto 5 GHz DFS channels reject the request outright, and
NetworkManager then stops trying. Keeping `autoconnect-retries-default=0`
(retry forever) is the fix that actually works; see
[Troubleshooting](../README.md#troubleshooting) if a specific router still
gives you trouble.

**How it was verified:** on the tested Mac, after purging broadcom-sta and
blacklisting the other drivers, `brcmfmac` was the only Broadcom driver
loaded and `iw` reported power saving off. With retries set to forever the
laptop reconnected by itself after drops caused by a router that steers it
to a 5 GHz DFS channel. Those settings were made on the single connection;
this tool applies the same values as NetworkManager defaults instead.

For the sleep script: after one resume, the tested Mac failed every later
suspend with `brcmf_pcie_pm_enter_D3: Timeout on response for entering D3
substate` and woke straight back up. With the script in place, a 46-second
timed sleep and a two-hour idle sleep both completed, and Wi-Fi reconnected
about 5 seconds after each wake.

## sleep: working suspend on the 15-inch models

**The problem:** on 15-inch models, closing the lid and reopening it often
leaves the screen black — the Mac is technically awake but shows nothing.

**The cause:** the AMD discrete GPU on these Macs does not resume properly
from S3 ("deep") sleep on Linux. Lighter `s2idle` sleep avoids that, but only
works reliably if PCIe devices stay out of the deepest power state (D3cold)
and PCIe bridges stay powered. The T1 chip also wakes the Mac straight back
up if left alone, so it needs its own wakeup disabled.

**What changes:**

- `/etc/systemd/sleep.conf.d/parrot-mbp2017.conf` (only when an AMD GPU is
  present) sets `MemorySleepMode=s2idle` under `[Sleep]`. Treated as already
  satisfied if `/proc/cmdline` already has `mem_sleep_default=s2idle`.
- `/etc/udev/rules.d/90-parrot-mbp2017-pci-pm.rules` (only with an AMD GPU)
  keeps PCI devices out of D3cold and keeps PCIe bridge power control on.
- `/etc/udev/rules.d/99-zz-parrot-mbp2017-t1-nowake.rules` (only with a T1)
  disables USB wakeup for the T1 (`idVendor=05ac`, `idProduct=8600`).
- Runs `udevadm control --reload` afterwards. Takes full effect after a
  reboot.

**Undo:** `remove sleep` deletes whichever of the three files it installed
and reloads udev rules the same way.

**Printed after installing:** the [t1-wake](#t1-wake-touch-bar-and-touch-id-after-sleep)
fix is what brings the Touch Bar and Touch ID back after waking, and
Thunderbolt logs (harmless) errors on resume. See
[hardware.md](hardware.md#known-hardware-limitations) for the s2idle power
draw and lid-wake behavior this sleep mode brings with it.

**How it was verified:** on the tested Mac, deep (S3) sleep never resumed:
amdgpu failed with `SMU load firmware failed` and a hung GPU reset. With
`mem_sleep_default=s2idle` on the kernel command line and the two PCI rules,
one timed test (`rtcwake -m freeze -s 30`) suspended and resumed cleanly with
no amdgpu errors, and Wi-Fi reconnected. Later, with t1bridge installed, a
46-second timed sleep and idle sleeps of up to two hours also resumed with
working graphics. Waking by opening the lid has not been checked on its
own. This tool sets s2idle through systemd's `MemorySleepMode` instead of
the kernel command line; both select the same mode.

## audio: internal speakers

**The problem:** the built-in speakers are silent.

**The cause:** the mainline `snd-hda-codec-cs8409` kernel driver recognizes
the Cirrus Logic CS8409 codec on these Macs but does not know how to drive
their speaker amplifiers.

**What changes:**

- Installs `build-essential dkms git patch wget` and
  `linux-headers-<running kernel>` if missing.
- Clones
  [davidjo/snd_hda_macbookpro](https://github.com/davidjo/snd_hda_macbookpro)
  to `/usr/local/src/snd_hda_macbookpro` and checks out the pinned commit
  `89b22ff90b86468b186706861dd18663562defa7` (verified with
  `git rev-parse HEAD` before continuing).
- Runs `./install.cirrus.driver.sh -i` from that clone, which builds and
  installs the driver through DKMS as module `snd_hda_macbookpro/0.1`, so it
  rebuilds automatically for new kernels (needs an internet connection each
  time, to fetch matching kernel source from cdn.kernel.org).
- Speakers work after a reboot.

**Undo:** `remove audio` runs `./install.cirrus.driver.sh -r` (which runs
`dkms remove` to restore the stock module) and deletes
`/usr/local/src/snd_hda_macbookpro`.

**Health check:** `status` warns if any installed kernel has headers but the
module isn't built for it — this is what catches "speakers went silent after
a kernel update" (see
[Troubleshooting](../README.md#troubleshooting)).

**How it was verified:** on the tested Mac, `dkms status snd_hda_macbookpro`
showed `installed` for the running kernel, and the internal speakers produced
sound after a reboot. Took about 20 seconds to build and install.

## input: palm rejection and Touch Bar touches

**The problem:** the built-in keyboard's trackpad doesn't ignore your palms
while typing, so resting a hand on the trackpad while typing can move the
cursor or click. On Macs with a T1, the Touch Bar can also show up as a
second trackpad.

**The cause:** the `applespi` driver reports the built-in keyboard's USB
vendor ID as `0x0000` instead of Apple's `0x05AC`, so libinput's built-in
`Apple Internal Keyboard (SPI)` quirk (which matches on `0x05AC`) never
applies, and libinput treats it as an external keyboard — which means it
never turns on disable-while-typing. Separately, in t1bridge mode the T1
exposes the Touch Bar's touch digitizer as a regular evdev touchpad, which
libinput would otherwise pick up as a second, unwanted trackpad.

**What changes:**

- Adds a managed block to `/etc/libinput/local-overrides.quirks` (marked with
  `# >>> parrot-mbp2017 input >>>` / `# <<< parrot-mbp2017 input <<<`, so any
  other content in that file is left alone) that marks the Apple SPI Keyboard
  as internal, only when the SPI keyboard is present.
- `/etc/udev/rules.d/90-parrot-mbp2017-touchbar-not-pointer.rules` (only when
  a T1 is present) tells libinput to ignore the T1's touch digitizer as a
  pointer device.
- Runs `udevadm control --reload` then
  `udevadm trigger --action=change --subsystem-match=input` afterwards. You
  need to log out and back in for it to fully apply.

**Undo:** `remove input` deletes the managed block (and the file itself, if
this tool created it and it's now empty) and the udev rule, then reloads udev.

**How it was verified:** on the tested Mac, `libinput debug-events --verbose`
printed `palm: dwt activated with Apple SPI Touchpad<->Apple SPI Keyboard`
(disable-while-typing working) and `event6: device is ignored` for the T1's
touch digitizer.

## t1bridge: Touch Bar, Touch ID, camera and ambient light sensor

**The problem:** without this fix, a Mac with a T1 chip has no Touch Bar
display, no Touch ID, and its FaceTime camera and ambient light sensor don't
work either — the T1 handles all of them, and nothing in a stock Parrot
install talks to it.

**The cause:** the T1 is a separate ARM chip that needs its own driver stack
and services to expose the Touch Bar, fingerprint sensor, camera and light
sensor to Linux. Upstream [t1bridge](https://github.com/standardagents/t1bridge)
(tag `v0.1.12`, commit `81cbdf81026a16e02f0bea74735c6b029a8ffae2`) provides
that, but only ships Arch Linux packages; this repository builds Debian
packages from that same pinned source.

**What changes:**

- Removes the legacy firmware-mode Touch Bar driver if present (DKMS module
  `apple-ib-drv`, `/usr/src/apple-ib-drv-*`,
  `/etc/modprobe.d/apple-touchbar.conf`,
  `/etc/modules-load.d/apple-touchbar.conf`,
  `/etc/udev/rules.d/99-ibridge.rules`) — it pins the T1 to a USB
  configuration that conflicts with t1bridge. Anything removed is saved under
  `/var/lib/parrot-mbp2017/legacy/` first.
- Gets five `.deb` packages — `t1bridge`, `t1bridge-dkms`, `libfprint-2-2`,
  `fprintd`, `libpam-fprintd` — from `--debs DIR` if given, otherwise a cached
  build in `/var/cache/parrot-mbp2017/t1bridge-<version>/`, otherwise builds
  them fresh with `packaging/t1bridge/build-debs.sh` (in Docker if available,
  about 5 minutes on 6 cores; natively with `--native` otherwise, which
  installs build dependencies with apt and briefly enables
  `<codename>-backports` deb-src for the build). Build output goes to
  `/var/log/parrot-mbp2017-build.log`.
- Installs all five packages with
  `apt-get install -y --allow-downgrades`, then
  `apt-mark hold libfprint-2-2 fprintd libpam-fprintd` so a routine update
  can't split the matched set.
- Adds the invoking user to the `t1bridge` group.
- If the stock `/usr/lib/udev/rules.d/39-usbmuxd.rules` still deconfigures the
  T1 (it sets `bConfigurationValue=0`), writes a
  `/etc/udev/rules.d/39-usbmuxd.rules` override with that part removed, and
  records the stock rule's checksum so `status` can warn if a package update
  changes it.

**Undo:** `remove t1bridge` removes the `t1bridge` and `t1bridge-dkms`
packages, unholds and reinstalls the distribution's own `libfprint-2-2`,
`fprintd` and `libpam-fprintd`, removes the `-dev`/`gir1.2` packages if they
came from this build, and deletes the usbmuxd override. It keeps
`/var/lib/t1bridge` (your fingerprint enrollment data).

**Afterwards, this fix prints:** reboot; check `sudo t1bridge status` (every
row should say `ready`); run
`sudo systemctl start t1bridge-import.service`; then, **as your normal user,
without sudo**, `fprintd-enroll -f right-index-finger` followed by
`fprintd-verify -f right-index-finger` — enrolling with sudo fails, see
[Troubleshooting](../README.md#troubleshooting).

**How it was verified:** on the tested Mac, all five packages installed at
the pinned version, `sudo t1bridge status` reported every row `ready`, the
Touch Bar displayed and responded to touch, an enrolled fingerprint worked
with `fprintd-verify`, the FaceTime camera captured a frame with `ffmpeg`
(it offers H.264 only), and the ambient light sensor gave readings.

## t1-wake: Touch Bar and Touch ID after sleep

**The problem:** after the Mac wakes from sleep, the Touch Bar stays blank
even though touching it still works, and the lock screen's fingerprint check
fails.

**The cause:** two separate things. t1bridge's display driver
(`appletbdrm`) parks the Touch Bar panel before sleep, by setting byte 1 of
HID feature report 3 on the T1's interface 6 to `1`, and nothing sets it
back to `2` after waking, so the panel stays off while touch input carries
on. And Touch ID's key service (`t1bridge-keybag.service`) loses its link to
the T1 during sleep and exits; systemd restarts it only after a delay that
grows with every sleep, up to a minute, and the lock screen asks for a
finger before it's back.

**What changes:**

- Installs `/usr/lib/systemd/system-sleep/parrot-mbp2017-t1-wake` (mode
  `0755`), a Python script systemd runs straight after waking, while your
  desktop is still paused. If the panel is parked, it switches it back on.
  If `t1bridge-keybag.service` has failed or is waiting to restart, it
  restarts it right away (waiting at most 20 seconds). It does nothing
  before sleep.

**Depends on:** `t1bridge`.

**Undo:** `remove t1-wake` deletes the script.

**Limits:** only light (s2idle) sleep, which the [sleep](#sleep-working-suspend-on-the-15-inch-models)
fix sets on 15-inch models, has been tested. Upstream reports that after
deep (S3) sleep the panel stays dark even when switched back on
([t1bridge#18](https://github.com/standardagents/t1bridge/issues/18)).

**How it was verified:** on the tested Mac, the panel reported itself parked
after waking and lit up again, as the owner confirmed, once switched back on.
With the script installed, the journal showed the panel switched back on and
the key service restarted within about a second of every wake, across a
failed sleep attempt, a 46-second timed sleep and idle sleeps of up to two
hours. The Touch Bar was lit after them without restarting anything else.
After the tool itself installed the fix, a 40-second timed sleep woke on the
lock screen with the panel back on and the key service restarted before the
desktop resumed; the owner unlocked with a fingerprint and the Touch Bar
buttons responded.

## desktop: Touch Bar controls and lock screen Touch ID in Plasma

**The problem:** with t1bridge alone, the Touch Bar doesn't show volume, mute
or media buttons, and changing brightness shows no on-screen popup — Plasma's
brightness slider also drifts out of sync with the actual screen brightness.
After logging out and back in, the Touch Bar can also freeze: it still
shows its buttons but ignores touches. And an enrolled fingerprint doesn't
unlock Plasma's lock screen, even though the lock screen says it accepts
one.

**The cause:** t1bridge ships no desktop integration by itself; it expects a
"desktop provider" plugin to translate its Touch Bar button events into
actions your desktop understands, and to tell it what to display. It also
changes brightness through logind rather than sending normal brightness key
presses, which is why Plasma's own brightness UI doesn't notice the change
unless something tells it to.

t1bridge's Touch Bar renderer, `t1-touchbar.service`, stops when you log out
but is only started when your user's systemd manager starts. That manager
keeps running for 10 seconds after you log out, and indefinitely while an
SSH session or lingering keeps it alive; log back in during that time and
the renderer is not started again.

The lock screen runs a separate fingerprint service, `kde-fingerprint`,
alongside the password box. Parrot's copy
(`/usr/lib/pam.d/kde-fingerprint`) runs `pam_kwallet5` after the finger
matches; with no password to open the wallet, `pam_kwallet5` asks for one,
the lock screen never answers, and the unlock hangs. Plasma 6.3 also stops
listening for the rest of a lock once a check reports "unavailable", which
happens whenever a check times out (the T1 ends each one after 30 seconds).

**What changes:**

- Installs `/usr/local/lib/parrot-mbp2017/desktop-provider` (mode `0755`),
  which implements t1bridge's desktop provider v1 contract: volume through
  `wpctl`, media keys over MPRIS via `busctl`, and brightness popups plus
  slider sync through Plasma's PowerDevil and OSD D-Bus services.
- Installs
  `/etc/systemd/user/t1-touchbar.service.d/parrot-mbp2017-desktop-provider.conf`,
  a drop-in that points `t1-touchbar.service` at that script via the
  `T1BRIDGE_DESKTOP_PROVIDER` environment variable.
- Best-effort reloads and restarts the invoking user's `t1-touchbar.service`
  (`systemctl --user -M <user>@ daemon-reload` and `try-restart`); if that
  doesn't apply cleanly, log out and back in instead.
- Installs
  `/etc/systemd/user/graphical-session.target.d/parrot-mbp2017-touchbar.conf`,
  which starts the renderer with every graphical session.
- With Plasma installed, installs `/etc/pam.d/kde-fingerprint`, which takes
  precedence over Parrot's copy. It is the same service without
  `pam_kwallet5` in the authentication step, and it reports a failed or
  timed-out check as a plain failure rather than "unavailable". The password
  box uses a different service, `kde`, which is not touched.

**Depends on:** `t1bridge` — this fix shows as `blocked` in `status` until
t1bridge is installed (or selected in the same `setup` run).

**Using Touch ID on the lock screen:** the lock screen listens for a finger
for 30 seconds after the Mac locks or wakes. After that, press Enter in the
empty password box, wait 3 seconds, then touch the sensor; Plasma only
starts a new check after a failed password attempt, and other key presses
don't count. The login screen you see after starting up or logging out asks
for your password; this fix does not change it.

**Undo:** `remove desktop` deletes its files and does the same reload/restart.

**How it was verified:** on the tested Mac under KDE Plasma 6.3, the owner
confirmed that the Touch Bar brightness buttons showed Plasma's popup again.
Calling the provider directly showed the screen brightness, keyboard
backlight and volume popups, left the hardware brightness unchanged (no
flicker), and brought Plasma's brightness slider back in sync with the
hardware. The media buttons have not been tested.

For the renderer, the owner logged out and back in while an SSH session kept
the user manager running. Before the change the Touch Bar froze on its last
frame; with it, the renderer started again at login and the buttons worked.

For the lock screen, the owner locked the Mac, pressed Shift, then touched
the sensor, and it unlocked; the fingerprint service reported a match and
the key service came back straight after. With Parrot's copy, the same match
left the lock screen hanging until a failed password attempt. After a
timed sleep, the check that started on waking timed out before the owner
reached the Mac; a failed password attempt started a new one, and the
fingerprint unlocked the lock screen.

## fingerprint-login: Touch ID for sudo and other password prompts

**The problem:** by default, an enrolled fingerprint works with the
`fprintd-verify` command-line tool and, with the `desktop` fix, Plasma's
lock screen, but not with `sudo` or other password prompts.

**The cause:** using a fingerprint for actual authentication needs the
distribution's PAM (Pluggable Authentication Modules) profile for fprintd
turned on; it isn't by default.

**Off by default** — this is the one opt-in fix, since it changes how you
authenticate as an administrator.

**What changes:**

- Warns if `fprintd-list <user>` shows no enrolled finger yet.
- Runs `pam-auth-update --enable fprintd`, which adds `pam_fprintd.so` to
  `/etc/pam.d/common-auth`. Your password keeps working as a fallback —
  nothing about password login is removed. Everything that includes
  `common-auth` asks for a finger first, for up to 10 seconds, before the
  password; that includes Plasma's lock screen password box.

**Depends on:** `t1bridge` (for the `libpam-fprintd` package and the fprintd
PAM profile it installs).

**Undo:** `remove fingerprint-login` runs `pam-auth-update --disable fprintd`,
which removes fingerprint authentication from `common-auth` and leaves
password login as the only method again.

**Before you enable it:** keep a root terminal open and test `sudo` in a new
terminal before closing the root one, in case something doesn't work as
expected. If fingerprint enrollment fails when you try it with `sudo`, that's
expected — see
[Troubleshooting](../README.md#troubleshooting): enroll as yourself, in your
own desktop session, without sudo.

**How it was verified:** not yet. It uses Debian's own `pam-auth-update`
profile for fprintd, which keeps password authentication as the fallback.
Test `sudo` in a new terminal while keeping another root terminal open.
