# T1 recovery

This page is for owners of a Mac with a T1 chip (the small ARM chip behind the
Touch Bar, Touch ID, camera, and ambient light sensor on 2016-2017 15-inch and
four-port 13-inch MacBook Pros). It explains what the T1 needs from the EFI
partition, how to back that up, and how to get it back if it is ever missing.

## What the T1 data is, and why it matters

The T1 keeps its firmware and your Touch ID enrollment identity in a folder
called `EFI/APPLE` on the Mac's EFI system partition (the small FAT partition
every Mac boots from). Without `EFI/APPLE/EMBEDDEDOS`, the T1 has no firmware
to run: the Touch Bar stays blank and Touch ID does not work.

Reinstalling Linux, repartitioning, or reformatting the EFI partition can wipe
this folder. Once it is gone, the only way to get it back is either a copy you
made beforehand, or an online recovery through Apple's servers, which needs
this exact Mac's identity, a charger, and an internet connection.

This is why the `t1-backup` fix exists, and why `parrot-mbp2017` backs this
data up by default when your Mac has it.

## How to tell what state your T1 is in

Two ways:

- `lsusb`: look for a device from Apple (`05ac`).
  - `05ac:8600` — the T1 is running normally (this is sometimes called
    "iBridge" mode).
  - `05ac:1281` — the T1 is in recovery mode: its firmware is missing. The
    Touch Bar will not work until it is restored.
- `sudo parrot-mbp2017 status` — shows the Touch Bar (T1) row with its state,
  and a T1 data row saying whether `EFI/APPLE` is present on the EFI
  partition. Run it with `sudo`; without root access the T1 data row cannot
  be read and will say "unknown, run with sudo".

## Backing up

Run `sudo parrot-mbp2017 install t1-backup` (or accept it in `setup`, since it
is on by default). This copies `EFI/APPLE` from the EFI partition into
`~/parrot-mbp2017-t1-backup` in the invoking user's home directory (override
with `--backup-dir DIR`), as a dated archive plus a checksum file. The backup
directory and its files are locked down (mode 0700 and 0600) so only that user
can read them.

**Copy this folder off the Mac** — a USB stick or private cloud storage — and
**never share it**. It contains this Mac's serial number and T1 identity,
which is enough to impersonate this specific Mac's T1 to Apple.

`sudo parrot-mbp2017 status` shows `done` for this fix once a backup matching
the current EFI partition contents exists.

## Restoring from a backup

If `EFI/APPLE/EMBEDDEDOS` is ever missing and you have a backup (from this
tool or from any other copy that still has the same folder layout), run:

```
sudo parrot-mbp2017 restore-t1 --from DIR
```

`DIR` can be either a `parrot-mbp2017-t1-backup` folder (with an
`EFI-APPLE-*.tar` archive and a `SHA256SUMS` file) or an already-extracted
copy that directly contains `EFI/APPLE/EMBEDDEDOS`. The tool verifies
checksums where it has them, and refuses to run if the EFI partition already
has `EFI/APPLE/EMBEDDEDOS` — it never overwrites existing T1 data.

Afterwards, **shut the Mac down completely and power it back on**. A warm
reboot does not reset the T1; only a full power cycle does. The T1 should
then show up as `05ac:8600`.

This restore path has not been exercised on real hardware yet. If you can,
keep the Mac powered and try `--from` before falling back to `--online`
below.

## Online regeneration (no backup)

If there is no backup and the T1 is in recovery mode (`05ac:1281`), the tool
can regenerate the firmware through Apple's own signing servers, using the
[t1-revive](https://github.com/niconistal/t1-revive) project:

```
sudo parrot-mbp2017 restore-t1 --online
```

What this does, before you confirm: **it sends this Mac's T1 identity to
Apple's servers** (`gs.apple.com`, `swcdn.apple.com`) so they can sign new
firmware for this specific device. It needs:

- the T1 in recovery mode (`05ac:1281`) with no existing `EFI/APPLE/EMBEDDEDOS`
- the charger plugged in
- a working internet connection
- the legacy `apple-ib-drv` Touch Bar driver *not* installed (it can wedge the
  restore partway through)

Building and running it takes about six minutes in total on the tested Mac.
Afterwards, as with a backup restore, **shut down completely and power back
on**, then run `sudo parrot-mbp2017 install t1-backup` to make a backup of the
firmware you just recovered, so you never need to do this again.

If it fails partway through, the command names the log file to check and
tells you not to retry blindly — the T1 can be left in a state that needs a
different next step depending on where it failed.

### Doing it manually

If you would rather run t1-revive yourself instead of through this tool:

1. Install its build dependencies (`autoconf automake libtool pkgconf git
   patch libzip-dev libusb-1.0-0-dev libssl-dev zlib1g-dev libreadline-dev
   acpi-call-dkms python3`, plus `libcurl4-openssl-dev` from your Parrot
   codename's `-backports` suite — from the main suite it conflicts with
   Parrot's own backported libcurl), then `sudo modprobe acpi_call`.
2. Stop `usbmuxd` if it is running: `sudo systemctl stop usbmuxd`.
3. Clone the project and check out the pinned commit used by this tool
   (`c2062f3a09b2d278649d3ec48bbb7d15c8b55bcf`), then run `bash build.sh`
   inside the clone as your normal user, not root.
4. Run `sudo bin/t1-revive preflight` and read its output. On Parrot the only
   expected `NO` line is an Arch Linux package check, which does not apply
   here; any other `NO` line means stop and investigate before continuing.
5. Back up the whole EFI partition yourself before continuing (for example
   `sudo tar -czf esp-backup.tar.gz -C /boot efi`).
6. Run `sudo bin/t1-revive --no-confirm regenerate` from inside the clone.
7. Check the T1 shows up as `05ac:8600` and that
   `EFI/APPLE/EMBEDDEDOS/FDRData`, `combined.memboot` and `version.plist`
   exist on the EFI partition. Shut down fully and power back on.

## Safety rules

- **Never share a T1 backup.** It contains this Mac's serial number and T1
  identity.
- **Never overwrite existing T1 data.** If `EFI/APPLE/EMBEDDEDOS` is already
  on the EFI partition, leave it alone — restoring on top of a working T1
  can leave it in a broken, hard-to-diagnose state.
- **Do not run any of this recovery on a T1 that is already working.** These
  steps are for a T1 in recovery mode (`05ac:1281`) or a missing
  `EFI/APPLE/EMBEDDEDOS` only. If the Touch Bar and Touch ID already work,
  there is nothing to recover — just make a backup with `t1-backup` instead.
