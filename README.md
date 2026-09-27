# parrot-mbp2017

A command-line tool that gets Parrot OS 7 working properly on 2016-2017
Touch Bar MacBook Pros: it detects your Mac's hardware, shows you which
fixes it needs, and applies only those — each one reversible.

- **For:** a 2016 or 2017 MacBook Pro (Touch Bar models especially) running
  Parrot OS 7.
- **Run:** `sudo ./parrot-mbp2017` and answer the prompts.
- **Undo:** `sudo ./parrot-mbp2017 remove <fix>` puts back whatever that fix
  changed.

## What you get

| Feature | State after setup |
| --- | --- |
| Wi-Fi | Stays connected; recovers on its own after a short outage |
| Sleep (15-inch models) | Suspend and resume work using the lighter s2idle mode (see [Known issues](#known-issues)) |
| Speakers | Internal speakers work |
| Touch Bar | Displays and responds to touch |
| Touch ID | Enrolled fingerprint works with `fprintd-verify` |
| FaceTime camera | Works — H.264 only, so some apps can't use it yet |
| Ambient light sensor | Works |
| Trackpad | Ignores resting palms while typing; Touch Bar isn't mistaken for a second trackpad |
| Touch Bar volume and brightness | Volume, mute and media buttons appear on the Touch Bar; brightness changes show the KDE Plasma popup and the brightness slider stays in sync |
| Fingerprint sudo and lock screen | Optional (off by default): an enrolled finger unlocks `sudo` and the lock screen too |

## Is my Mac supported?

`parrot-mbp2017` only changes anything on a Mac it recognizes. Check your
model first:

```
cat /sys/class/dmi/id/product_name
```

| Model | Description | T1, Touch Bar | Status |
| --- | --- | --- | --- |
| MacBookPro13,1 | 13-inch, 2016, two Thunderbolt 3 ports | no | untested |
| MacBookPro13,2 | 13-inch, 2016, four Thunderbolt 3 ports | yes | untested |
| MacBookPro13,3 | 15-inch, 2016 | yes | untested |
| MacBookPro14,1 | 13-inch, 2017, two Thunderbolt 3 ports | no | untested |
| MacBookPro14,2 | 13-inch, 2017, four Thunderbolt 3 ports | yes | untested |
| MacBookPro14,3 | 15-inch, 2017 | yes | **tested** |

Only MacBookPro14,3 has been run through this tool on real hardware.
The other models are supported by the same detection and the same fixes —
each fix only runs when its hardware is actually present, not based on the
model name — but treat them as unproven until confirmed. Any other Mac, or
a non-Apple machine, is refused unless you pass `--force`. See
[docs/hardware.md](docs/hardware.md) for exactly what's detected and how.

## Quick start

```
git clone https://github.com/Duresa7/parrot-mbp2017.git
cd parrot-mbp2017
sudo ./parrot-mbp2017
```

This runs the setup wizard. It shows your hardware, a table of fixes with
their current status, and a recommended selection you can adjust before
anything changes:

```
parrot-mbp2017 0.1.0
Sets up Parrot OS on 2016-2017 Touch Bar MacBook Pros. Every change can be undone.
Hardware        Detected
Model           MacBookPro14,3 — 15-inch, 2017
System          Parrot Security 7.3 (echo), kernel 7.0.9+parrot7-amd64
Graphics        Intel + AMD Radeon Pro
Wi-Fi           Broadcom BCM43602 (brcmfmac)
Sound           Cirrus Logic CS8409
Touch Bar (T1)  running, USB configuration 2
...
#  Fix                Status       Summary
1  t1-backup          not set up   Save a copy of the T1's firmware and Touch ID data from the EFI partition.
2  wifi               not set up   Prevent driver conflicts, turn off Wi-Fi power saving and keep retrying connections.
3  sleep              partly done  Use lighter sleep for AMD graphics and stop the T1 waking the Mac immediately.
...
1. [x] t1-backup: Back up the T1 firmware and Touch ID data
2. [x] wifi: Stable Wi-Fi on the Broadcom BCM43602
3. [x] sleep: Working suspend on the 15-inch models
...
8. [ ] fingerprint-login: Touch ID for sudo and the lock screen
Type numbers to toggle (separated by spaces), or press Enter to accept:
```

(Real output from the tested Mac, shortened.) Want to see the plan without
changing anything? Add `--dry-run`: `sudo ./parrot-mbp2017 --dry-run`.

Toggle any fix by its number, confirm, and it applies them in order, telling
you what it's doing and why as it goes. Building the T1 packages is the
slowest step — a few minutes with Docker installed, longer without it.

**After it finishes:**

1. Reboot — several fixes only take full effect after a reboot.
2. Enroll a fingerprint as *yourself*, without sudo:
   `fprintd-enroll -f right-index-finger`, then
   `fprintd-verify -f right-index-finger`.
3. Check everything: `sudo ./parrot-mbp2017 status`.

## Before you start

- **Back up first.** This tool is reversible, but you're still changing
  system files, kernel drivers and package holds on a machine you rely on.
- **Keep the charger plugged in.** A couple of steps (building drivers,
  restoring T1 firmware) shouldn't be interrupted by a dead battery.
- **You need internet access.** Packages are downloaded, and some drivers
  are built from source.
- **You need git** to download the tool: `sudo apt install git` if
  `git --version` says it is missing. Python 3 is already part of Parrot OS.
- **Time:** package building takes about 5 minutes with Docker installed, or
  longer if it builds natively instead. Everything else is quick.
- **It never touches SSH, user accounts, or anything outside these specific
  hardware fixes.**
- **Every change can be undone** — see [Undo](#undo) below.

## What each fix does

- **t1-backup** — backs up the T1's firmware and Touch ID data from the EFI
  partition, so a reinstall doesn't strand your Touch Bar and Touch ID. On by
  default when your Mac has a T1 and that data is present.
- **wifi** — keeps the Broadcom Wi-Fi chip on its correct driver, turns off
  power saving, and makes it retry forever instead of giving up after a few
  failed attempts.
- **sleep** — switches 15-inch models to a lighter sleep mode so the screen
  reliably returns after the lid closes, and stops the T1 waking the Mac back
  up immediately.
- **audio** — builds and installs the out-of-tree driver the internal
  speakers need, since the mainline driver doesn't drive their amplifiers.
- **input** — stops resting palms from moving the cursor while typing, and
  keeps the Touch Bar from being mistaken for a second trackpad.
- **t1bridge** — installs the drivers and services that let Linux talk to
  the T1 chip at all: the Touch Bar, Touch ID, camera and ambient light
  sensor all depend on this.
- **desktop** — connects the Touch Bar's volume, mute and media buttons, and
  brightness popups, to your desktop. Needs t1bridge first.
- **fingerprint-login** — lets an enrolled fingerprint authenticate `sudo`
  and the lock screen, on top of your password. Off by default, since it
  changes how you log in as an administrator.

Full detail — exact files, packages and commands for each — is in
[docs/fixes.md](docs/fixes.md).

## Commands

| Command | What it does |
| --- | --- |
| `parrot-mbp2017` | Same as `setup` |
| `setup` | Interactive wizard: detect hardware, show status, pick fixes, apply them |
| `status` | Hardware summary, fix status and health checks (works without root) |
| `detect` | Hardware summary only |
| `install FIX...` | Apply specific fixes by id |
| `remove FIX...` | Undo specific fixes by id |
| `restore-t1 --from DIR` | Restore T1 firmware from a backup |
| `restore-t1 --online` | Regenerate lost T1 firmware through Apple's servers |
| `list` | Every fix, with what it does and why |

Useful options:

| Option | Effect |
| --- | --- |
| `--dry-run` | Preview what would change, without changing anything |
| `--yes` | Accept the recommended selection and skip confirmation prompts |
| `--only IDS`, `--skip IDS` | Limit `setup` to, or exclude, a comma-separated list of fix ids |
| `--debs DIR` | Use pre-built t1bridge `.deb` packages instead of building them |
| `--backup-dir DIR` | Where `t1-backup` stores the T1 backup |
| `--json` | Machine-readable output for `status` and `detect` |
| `--force` | Apply fixes on hardware this tool doesn't recognize |

Run `./parrot-mbp2017 --help` or `./parrot-mbp2017 <command> --help` for the
full list.

## Undo

```
sudo ./parrot-mbp2017 remove <fix>
```

Each fix restores whatever it changed: a file it replaced is put back, a
managed block is removed from a shared file, packages are reinstalled at
their distribution version. Two things are deliberately kept:

- **T1 backups** are never deleted by `remove t1-backup` — you're told where
  they are instead.
- **Fingerprint enrollment data** (`/var/lib/t1bridge`) is kept when you
  `remove t1bridge`, in case you reinstall it later.

## Troubleshooting

- **Touch Bar stays dark.** Run `sudo t1bridge status` and check every row
  says `ready`. If the T1 shows as `05ac:1281` (recovery mode), its firmware
  is missing — see [docs/t1-recovery.md](docs/t1-recovery.md).
- **Fingerprint enrollment fails.** Enroll as yourself, in your own desktop
  session, without sudo: `fprintd-enroll -f right-index-finger`. Enrolling
  with sudo does not work.
- **Speakers go silent after a kernel update.** The driver needs kernel
  headers and an internet connection to rebuild itself through DKMS for the
  new kernel. `sudo ./parrot-mbp2017 status` warns when a kernel is missing
  the built module.
- **Wi-Fi drops on routers that push 5 GHz DFS channels.** This tool keeps
  Wi-Fi retrying forever rather than giving up — let it reconnect. Do not
  lock the connection to 2.4 GHz; that was tried and made things worse (see
  [docs/fixes.md](docs/fixes.md#wifi-stable-wi-fi-on-the-broadcom-bcm43602)).
- **Touch Bar or Touch ID stop responding after sleep.** Known upstream
  limit: t1bridge doesn't yet keep the T1 connected through a sleep cycle.
  Reboot to bring them back.
- **Camera isn't listed in some apps.** It only outputs H.264, which not
  every application supports yet.

## Known issues

- Bluetooth patch firmware is missing, but Bluetooth still works with the
  in-tree driver.
- Thunderbolt logs errors on resume from sleep; Thunderbolt devices keep
  working.
- Other owners report that s2idle sleep draws about 7-13 W, far more than
  macOS's deep sleep, and that opening the lid alone does not wake the Mac
  (a key press does). Neither has been checked on the tested Mac yet.
- Sleep has been tested once with a timed wake, before t1bridge was
  installed. Longer sleeps and sleep with t1bridge are untested.
- The T1 backup-restore path (`restore-t1 --from DIR`) hasn't been exercised
  on real hardware yet; the online path (`--online`) has.

See [docs/hardware.md](docs/hardware.md) for more on all of these.

## Credits

This tool packages and glues together the work of several upstream
projects:

- [t1bridge](https://github.com/standardagents/t1bridge) — Linux support for
  the T1 chip: Touch Bar, Touch ID, camera and ambient light sensor.
- [snd_hda_macbookpro](https://github.com/davidjo/snd_hda_macbookpro) — the
  out-of-tree driver for the internal speaker amplifiers.
- [t1-revive](https://github.com/niconistal/t1-revive) — online T1 firmware
  regeneration through Apple's signing servers.
- [libinput](https://gitlab.freedesktop.org/libinput/libinput) — the input
  handling this tool configures for palm rejection and the Touch Bar.

It also draws on hardware research from the
[omarchy](https://github.com/basecamp/omarchy) and
[mbp-2016-linux](https://github.com/Dunedan/mbp-2016-linux) communities.

## License

MIT — see [LICENSE](LICENSE).
