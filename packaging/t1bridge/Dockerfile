# Build environment matching Parrot OS 7 (Debian 13 "trixie" base).
FROM parrotsec/core:latest

ENV DEBIAN_FRONTEND=noninteractive
ENV PARROT_MBP2017_BUILDER=1

# The image pins the main release at priority 1001, which installed Parrot
# systems don't; it would downgrade the backported Rust toolchain below 1.88.
RUN rm -f /etc/apt/preferences.d/99parrot-release \
    && echo "deb-src https://deb.parrot.sh/parrot echo main" \
        > /etc/apt/sources.list.d/parrot-src.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl devscripts dpkg-dev equivs \
        fakeroot git pkgconf quilt \
        libarchive-dev liblzma-dev libssl-dev libsystemd-dev libudev-dev \
        systemd-dev \
    && apt-get install -y --no-install-recommends -t echo-backports \
        cargo rustc libcurl4-openssl-dev \
    && apt-get build-dep -y libfprint fprintd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
