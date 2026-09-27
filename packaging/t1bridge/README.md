# t1bridge Debian packages

Builds `t1bridge`, `t1bridge-dkms`, `libfprint-2-2`, `fprintd` and
`libpam-fprintd` for amd64 Parrot OS 7 / Debian 13. The libfprint build also
produces `libfprint-2-dev` and `gir1.2-fprint-2.0`. The fingerprint packages
include upstream's T1 driver and duplicate-detection patches; fprintd depends
on the exact patched libfprint version.

From the repository root, build with Docker:

```sh
packaging/t1bridge/build-debs.sh
```

Or run as root on a Parrot/Debian 13 build host with its
`<VERSION_CODENAME>-backports` repository enabled:

```sh
packaging/t1bridge/build-debs.sh --native
```

Native builds install build dependencies and the patched libfprint runtime,
headers and introspection package on the host. If no enabled `deb-src` entry
exists (including deb822 sources), the script temporarily adds
`/etc/apt/sources.list.d/parrot-mbp2017-src.list`, refreshes apt, and removes
that entry and refreshes apt again on exit. Docker builds use the prepared
image and refresh its package lists without reinstalling build dependencies.

Output defaults to `dist/t1bridge/`. Use `--out DIR` to override it. The final
`== done:` line lists the resulting `.deb` files. `HOST_UID` and `HOST_GID`
control output ownership; the Docker wrapper defaults to the invoking user's
IDs. Native builds otherwise leave output owned by root.

The script clones upstream tag `v0.1.12` from
<https://github.com/standardagents/t1bridge> and requires commit
`81cbdf81026a16e02f0bea74735c6b029a8ffae2`. Use `--src DIR` with an existing
clean checkout at that commit to skip cloning; its HEAD is still verified.
In Docker mode that directory is mounted read-only at `/upstream`.

To bump upstream, update `T1BRIDGE_TAG` and `T1BRIDGE_COMMIT` together in
`build-debs.sh`. Review the pinned Arch PKGBUILDs, patches, cargo targets and
staged files. Check that the libfprint snapshot still matches the hard-coded
`date=20260829` and `1.94.9` version components before building. Source pins,
checksums and t1bridge's package version come from those Arch PKGBUILDs.
The t1bridge packages use revision `1~parrot1`; the fingerprint packages keep
the fork's `1~t1bridge1` / `+t1bridge1` version suffixes.
