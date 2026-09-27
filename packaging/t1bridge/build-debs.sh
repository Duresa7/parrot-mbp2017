#!/usr/bin/env bash
# Build Debian packages using the pinned upstream Arch source pins and patches.
# Usage: build-debs.sh [--native] [--out DIR] [--src DIR]
set -euo pipefail

T1BRIDGE_REPO=https://github.com/standardagents/t1bridge.git
T1BRIDGE_TAG=v0.1.12
T1BRIDGE_COMMIT=81cbdf81026a16e02f0bea74735c6b029a8ffae2

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
out="$root/dist/t1bridge"
source_dir=
native=0
image=parrot-mbp2017-t1bridge-build

usage() {
	printf 'Usage: %s [--native] [--out DIR] [--src DIR]\n' "$0"
}

while [ "$#" -gt 0 ]; do
	case "$1" in
	--native)
		native=1
		shift
		;;
	--out|--src)
		if [ "$#" -lt 2 ] || [ -z "$2" ] || [[ "$2" == --* ]]; then
			printf 'Missing directory for %s\n' "$1" >&2
			usage >&2
			exit 2
		fi
		if [ "$1" = --out ]; then
			out=$2
		else
			source_dir=$2
		fi
		shift 2
		;;
	-h|--help)
		usage
		exit 0
		;;
	*)
		printf 'Unknown argument: %s\n' "$1" >&2
		usage >&2
		exit 2
		;;
	esac
done

if [ -n "$source_dir" ]; then
	source_dir=$(cd "$source_dir" && pwd)
fi
mkdir -p "$out"
out=$(cd "$out" && pwd)

if [ "$native" -eq 0 ]; then
	echo "== building container image"
	docker build -t "$image" "$here"
	source_mount=()
	source_args=()
	if [ -n "$source_dir" ]; then
		source_mount=(-v "$source_dir:/upstream:ro")
		source_args=(--src /upstream)
	fi
	exec docker run --rm \
		-v "$root:/work:ro" -v "$out:/out" "${source_mount[@]}" \
		-e HOST_UID="${HOST_UID:-$(id -u)}" -e HOST_GID="${HOST_GID:-$(id -g)}" \
		"$image" bash /work/packaging/t1bridge/build-debs.sh \
		--native --out /out "${source_args[@]}"
fi

# Building fprintd installs our patched libfprint headers into the build host.
if [ "$(id -u)" -ne 0 ]; then
	echo "Native builds need root to install build dependencies and patched libfprint headers." >&2
	exit 1
fi

work=$(mktemp -d)
source_list=/etc/apt/sources.list.d/parrot-mbp2017-src.list
added_source=0
build_complete=0
cleanup() {
	local status=$?
	trap - EXIT
	cd / || true
	rm -rf "$work" || status=1
	if [ "$added_source" -eq 1 ]; then
		rm -f "$source_list" || status=1
		apt-get update || status=1
	fi
	if [ "$status" -eq 0 ] && [ "$build_complete" -eq 1 ]; then
		printf '== done:'
		printf ' %s' "$out"/*.deb
		printf '\n'
	fi
	exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# shellcheck source=/dev/null
. /etc/os-release
codename=${VERSION_CODENAME:?Missing VERSION_CODENAME in /etc/os-release}

# Recognize both traditional .list entries and enabled deb822 .sources stanzas.
has_source_repository() {
	local file
	for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list; do
		[ -f "$file" ] || continue
		if grep -Eq '^[[:space:]]*deb-src[[:space:]]' "$file"; then
			return 0
		fi
	done
	for file in /etc/apt/sources.list.d/*.sources; do
		[ -f "$file" ] || continue
		if awk '
			BEGIN { RS=""; FS="\n" }
			{
				src=0; disabled=0
				for (i=1; i<=NF; i++) {
					if ($i ~ /^Types:.*[[:space:]]deb-src([[:space:]]|$)/) src=1
					if (tolower($i) ~ /^enabled:[[:space:]]*no([[:space:]]|$)/) disabled=1
				}
				if (src && !disabled) found=1
			}
			END { exit !found }
		' "$file"; then
			return 0
		fi
	done
	return 1
}

if [ "${PARROT_MBP2017_BUILDER:-}" = 1 ]; then
	# The Docker image drops package lists; apt-get source still needs them.
	echo "== refreshing source package lists"
	apt-get update -qq
else
	echo "== preparing native build dependencies"
	if ! has_source_repository; then
		if [ -e "$source_list" ] || [ -L "$source_list" ]; then
			printf 'Cannot create temporary source entry: %s already exists. Enable deb-src there first.\n' "$source_list" >&2
			exit 1
		fi
		source_mirror=https://deb.debian.org/debian
		if [ "${ID:-}" = parrot ]; then
			source_mirror=https://deb.parrot.sh/parrot
		fi
		# noclobber also protects against a source file created concurrently.
		(set -o noclobber; printf 'deb-src %s %s main\n' "$source_mirror" "$codename" > "$source_list")
		added_source=1
	fi
	apt-get update
	apt-get install -y --no-install-recommends \
		build-essential ca-certificates curl devscripts dpkg-dev equivs \
		fakeroot git pkgconf quilt \
		libarchive-dev liblzma-dev libssl-dev libsystemd-dev libudev-dev \
		systemd-dev
	apt-get install -y --no-install-recommends -t "${codename}-backports" \
		cargo rustc libcurl4-openssl-dev
	apt-get build-dep -y libfprint fprintd
fi

if [ -z "$source_dir" ]; then
	echo "== fetching t1bridge $T1BRIDGE_TAG"
	source_dir="$work/t1bridge"
	git clone --depth 1 --branch "$T1BRIDGE_TAG" "$T1BRIDGE_REPO" "$source_dir"
fi
echo "== verifying t1bridge $T1BRIDGE_TAG"
actual_commit=$(git -c safe.directory="$source_dir" -C "$source_dir" rev-parse HEAD)
if [ "$actual_commit" != "$T1BRIDGE_COMMIT" ]; then
	printf 'Upstream commit mismatch: expected %s (%s), got %s in %s\n' \
		"$T1BRIDGE_COMMIT" "$T1BRIDGE_TAG" "$actual_commit" "$source_dir" >&2
	exit 1
fi
arch_dir="$source_dir/packaging/arch"

pkgbuild_var() { # pkgbuild_var FILE NAME
	sed -n "s/^$2=['\"]\{0,1\}\([^'\"]*\)['\"]\{0,1\}$/\1/p" "$1" | head -n 1
}

t1_ver=$(pkgbuild_var "$arch_dir/t1bridge/PKGBUILD" pkgver)
deb_rev=1~parrot1

# ------------------------------------------------------------------ libfprint ----
build_libfprint() {
	local pb="$arch_dir/libfprint-t1bridge/PKGBUILD"
	local commit sha short date ver
	commit=$(pkgbuild_var "$pb" _commit)
	sha=$(sed -n '/^sha256sums=/{s/.*(.\([0-9a-f]\{64\}\).*/\1/p}' "$pb")
	short=${commit:0:7}
	date=20260829
	ver="1:1.94.9+git${date}.${short}-1~t1bridge1"

	echo "== libfprint $ver"
	cd "$work"
	apt-get source --download-only libfprint >/dev/null
	dpkg-source -x libfprint_*.dsc distro-libfprint >/dev/null

	curl -fsSL -o "libfprint-${commit}.tar.gz" \
		"https://gitlab.freedesktop.org/libfprint/libfprint/-/archive/${commit}/libfprint-${commit}.tar.gz"
	echo "${sha}  libfprint-${commit}.tar.gz" | sha256sum -c -

	local upver="1.94.9+git${date}.${short}"
	cp "libfprint-${commit}.tar.gz" "libfprint_${upver}.orig.tar.gz"
	tar xzf "libfprint-${commit}.tar.gz"
	mv "libfprint-${commit}" "libfprint-${upver}"
	cp -a distro-libfprint/debian "libfprint-${upver}/"
	cd "libfprint-${upver}"

	mkdir -p debian/patches
	cp "$arch_dir/libfprint-t1bridge/"000*.patch debian/patches/
	(cd debian/patches && ls 000*.patch) >> debian/patches/series
	# The driver adds public API; don't fail the build on the new symbols.
	sed -i 's/^export DPKG_GENSYMBOLS_CHECK_LEVEL = 2/export DPKG_GENSYMBOLS_CHECK_LEVEL = 1/' debian/rules

	DEBFULLNAME="parrot-mbp2017" DEBEMAIL="parrot-mbp2017@localhost" \
		dch -v "$ver" -D "$codename" --force-distribution \
		"Upstream snapshot ${short} with the T1Bridge fingerprint driver."
	DEB_BUILD_OPTIONS="nocheck" dpkg-buildpackage -b -uc -us -j"$(nproc)"
	cd "$work"
	cp libfprint-2-2_*.deb libfprint-2-dev_*.deb gir1.2-fprint-2.0_*.deb "$out/"
	# fprintd builds against the patched headers.
	apt-get install -y ./libfprint-2-2_*.deb ./libfprint-2-dev_*.deb ./gir1.2-fprint-2.0_*.deb
	libfprint_ver=$ver
}

# -------------------------------------------------------------------- fprintd ----
build_fprintd() {
	echo "== fprintd"
	cd "$work"
	apt-get source --download-only fprintd >/dev/null
	dpkg-source -x fprintd_*.dsc fprintd-src >/dev/null
	cd fprintd-src

	cp "$arch_dir/fprintd-t1bridge/0001-Honor-driver-native-duplicate-detection.patch" debian/patches/
	echo 0001-Honor-driver-native-duplicate-detection.patch >> debian/patches/series
	# Keep fprintd and libfprint a matched pair, as the Arch packages do.
	sed -i "/^Package: fprintd$/,/^Depends:/{s/^Depends: /Depends: libfprint-2-2 (= ${libfprint_ver}), /}" debian/control

	local base
	base=$(dpkg-parsechangelog -S Version)
	DEBFULLNAME="parrot-mbp2017" DEBEMAIL="parrot-mbp2017@localhost" \
		dch -v "${base}+t1bridge1" -D "$codename" --force-distribution \
		"Honor driver-native duplicate detection (T1Bridge)."
	DEB_BUILD_OPTIONS="nocheck" dpkg-buildpackage -b -uc -us -j"$(nproc)"
	cd "$work"
	cp fprintd_*.deb libpam-fprintd_*.deb "$out/"
}

# ------------------------------------------------------------------- t1bridge ----
build_t1bridge() {
	echo "== t1bridge $t1_ver"
	cd "$source_dir"
	export CARGO_TARGET_DIR="$work/target"
	cargo build --locked --release -p t1-touchbar-hw --bin t1-touchbar-hw --features service
	cargo build --locked --release -p t1-touchbar --bin t1-touchbar
	cargo build --locked --release -p t1-daemons --bin t1-xart-storage
	cargo build --locked --release -p t1-daemons --bin t1-ncm-ready
	cargo build --locked --release -p t1-daemons --bin t1-keybag-relay --features keybag-relay-service
	cargo build --locked --release -p t1-daemons --bin t1-touchid-auth --features auth-broker-service
	cargo build --locked --release -p t1-daemons --bin t1-touchid
	cargo build --locked --release -p t1-daemons --bin t1-legacy-recovery --features legacy-recovery
	cargo build --locked --release -p t1-import --bin t1bridge

	local rel="$CARGO_TARGET_DIR/release" pkg="$work/pkg-t1bridge"
	local b
	for b in t1-touchbar-hw t1-touchbar t1-xart-storage t1-ncm-ready t1-keybag-relay t1-touchid-auth; do
		install -Dm755 "$rel/$b" "$pkg/usr/lib/t1bridge/$b"
	done
	install -Dm750 "$rel/t1-legacy-recovery" "$pkg/usr/lib/t1bridge/t1-legacy-recovery"
	install -Dm755 "$rel/t1-touchid" "$pkg/usr/bin/t1-touchid"
	install -Dm755 "$rel/t1bridge" "$pkg/usr/bin/t1bridge"
	strip --strip-unneeded "$pkg"/usr/lib/t1bridge/* "$pkg"/usr/bin/*

	local s="$source_dir/systemd" sys="$pkg/usr/lib/systemd/system"
	for f in t1-touchbar-hw.service t1-touchbar-hw.socket t1-xart-storage@.service \
		t1-ncm-ready@.service t1bridge-keybag.service t1-touchid-auth.service \
		t1-touchid-auth.socket t1bridge-fingerprint.socket t1bridge-import.service; do
		install -Dm644 "$s/$f" "$sys/$f"
	done
	install -Dm644 "$s/user/t1-touchbar.service" "$pkg/usr/lib/systemd/user/t1-touchbar.service"
	install -Dm644 "$s/80-t1bridge.preset" "$pkg/usr/lib/systemd/system-preset/80-t1bridge.preset"
	install -Dm644 "$s/user/80-t1bridge.preset" "$pkg/usr/lib/systemd/user-preset/80-t1bridge.preset"
	install -Dm644 "$s/t1bridge.sysusers" "$pkg/usr/lib/sysusers.d/t1bridge.conf"
	install -Dm644 "$s/t1bridge.conf" "$pkg/usr/lib/tmpfiles.d/t1bridge.conf"
	install -Dm644 "$s/50-t1bridge-ncm.link" "$pkg/usr/lib/systemd/network/50-t1bridge-ncm.link"
	install -Dm644 "$s/99-zz-t1bridge-touchbar.rules" "$pkg/usr/lib/udev/rules.d/99-zz-t1bridge-touchbar.rules"
	install -Dm644 "$s/90-t1bridge-xart.rules" "$pkg/usr/lib/udev/rules.d/90-t1bridge-xart.rules"
	install -Dm644 "$source_dir/LICENSE" "$pkg/usr/share/doc/t1bridge/copyright"
	install -Dm644 "$source_dir/THIRD_PARTY_NOTICES.md" "$pkg/usr/share/doc/t1bridge/THIRD_PARTY_NOTICES.md"

	# Runtime library dependencies, computed from the binaries.
	local shlibs
	mkdir -p "$work/shlibs/debian"
	printf 'Source: t1bridge\n\nPackage: t1bridge\nArchitecture: amd64\n' > "$work/shlibs/debian/control"
	shlibs=$(cd "$work/shlibs" && dpkg-shlibdeps -O "$pkg"/usr/lib/t1bridge/* "$pkg"/usr/bin/* 2>/dev/null \
		| sed -n 's/^shlibs:Depends=//p')

	mkdir -p "$pkg/DEBIAN"
	cat > "$pkg/DEBIAN/control" <<-EOF
	Package: t1bridge
	Version: ${t1_ver}-${deb_rev}
	Architecture: amd64
	Maintainer: parrot-mbp2017 <parrot-mbp2017@localhost>
	Depends: ${shlibs}, systemd (>= 256), iio-sensor-proxy
	Recommends: t1bridge-dkms (= ${t1_ver}-${deb_rev})
	Section: admin
	Priority: optional
	Homepage: https://github.com/standardagents/t1bridge
	Description: Apple T1 iBridge userspace services
	 Touch Bar renderer and hardware service, Touch ID broker, private T1 network
	 and xART storage services, and the Apple machine-data importer for 2016-2017
	 Touch Bar MacBook Pros. Parrot OS / Debian 13 port of the Arch packages.
	EOF
	install -m755 "$here/debian/t1bridge/postinst" "$here/debian/t1bridge/prerm" "$here/debian/t1bridge/postrm" "$pkg/DEBIAN/"
	dpkg-deb --root-owner-group --build "$pkg" "$out/t1bridge_${t1_ver}-${deb_rev}_amd64.deb"
}

# -------------------------------------------------------------- t1bridge-dkms ----
build_dkms() {
	echo "== t1bridge-dkms $t1_ver"
	local pkg="$work/pkg-dkms" src
	src="$pkg/usr/src/t1bridge-dkms-${t1_ver}"
	install -d "$src/kernel/t1-cfgsel" "$src/kernel/appletbdrm-t1" \
		"$src/kernel/apple-t1-ncm" "$src/kernel/uvcvideo-t1" "$src/kernel/dkms"
	local k="$source_dir/kernel"
	install -m644 "$k/t1-cfgsel/"{Makefile,t1_cfgsel.c,t1_recovery_reset.h} "$src/kernel/t1-cfgsel/"
	install -m644 "$k/appletbdrm-t1/"{Makefile,appletbdrm.c,appletbdrm_resume.h,appletbdrm_protocol.h} "$src/kernel/appletbdrm-t1/"
	install -m644 "$k/apple-t1-ncm/"{Makefile,apple_t1_ncm.c} "$src/kernel/apple-t1-ncm/"
	install -m644 "$k/uvcvideo-t1/"{Makefile,uvc_*.c,uvc_*.h,uvcvideo.h} "$src/kernel/uvcvideo-t1/"
	install -m644 "$k/dkms/Makefile" "$src/kernel/dkms/Makefile"
	sed "s/^PACKAGE_VERSION=.*/PACKAGE_VERSION=\"${t1_ver}\"/" "$k/dkms/dkms.conf" > "$src/dkms.conf"

	local a="$arch_dir/t1bridge-dkms"
	install -Dm644 "$a/t1bridge.modules-load.conf" "$pkg/usr/lib/modules-load.d/t1bridge.conf"
	install -Dm644 "$a/t1bridge.modprobe.conf" "$pkg/usr/lib/modprobe.d/t1bridge.conf"
	install -Dm644 "$here/debian/t1bridge-dkms/initramfs-modules.conf" \
		"$pkg/usr/share/initramfs-tools/modules.d/t1bridge.conf"
	install -Dm644 "$k/LICENSE.md" "$pkg/usr/share/doc/t1bridge-dkms/copyright"

	mkdir -p "$pkg/DEBIAN"
	cat > "$pkg/DEBIAN/control" <<-EOF
	Package: t1bridge-dkms
	Version: ${t1_ver}-${deb_rev}
	Architecture: all
	Maintainer: parrot-mbp2017 <parrot-mbp2017@localhost>
	Depends: dkms, initramfs-tools
	Section: kernel
	Priority: optional
	Homepage: https://github.com/standardagents/t1bridge
	Description: Kernel modules for the Apple T1 iBridge (DKMS)
	 t1_cfgsel (iBridge USB configuration selector), appletbdrm (Touch Bar
	 display), apple_t1_ncm (private T1 network link) and a uvcvideo build with
	 the T1 camera's H.264 descriptor support.
	EOF
	for f in postinst prerm postrm; do
		sed "s/@VERSION@/${t1_ver}/" "$here/debian/t1bridge-dkms/$f" > "$pkg/DEBIAN/$f"
		chmod 755 "$pkg/DEBIAN/$f"
	done
	dpkg-deb --root-owner-group --build "$pkg" "$out/t1bridge-dkms_${t1_ver}-${deb_rev}_all.deb"
}

build_libfprint
build_fprintd
build_t1bridge
build_dkms

if [ -n "${HOST_UID:-}" ]; then
	chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" "$out"
fi
build_complete=1
