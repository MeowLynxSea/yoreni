#!/bin/sh
# Fetch the binaries the Android app ships, none of which belong in git.
#
#   ./tools/fetch-assets.sh          # everything
#
# Downloads proot, an Ubuntu rootfs and a standalone CPython, then stages them
# where Gradle expects them. Re-running is safe: finished files are reused.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
MAIN="$ROOT/app/src/main"
CACHE=${YORENI_ANDROID_CACHE:-"$ROOT/.cache"}
TERMUX=https://packages.termux.dev/apt/termux-main
UBUNTU=https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release
PYTHON_TAG=20261026
PYTHON_BUILD=cpython-3.13.18+20261026-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz
PYTHON_ASSET=678576295

mkdir -p "$CACHE" "$MAIN/assets" "$MAIN/jniLibs/arm64-v8a"

fetch() {
  [ -f "$CACHE/$2" ] || curl -fsSL --retry 3 -o "$CACHE/$2" "$1"
}

echo "== proot"
fetch "$TERMUX/pool/main/p/proot/proot_5.1.107.102_aarch64.deb" proot.deb
fetch "$TERMUX/pool/main/liba/libandroid-shmem/libandroid-shmem_0.7_aarch64.deb" shmem.deb
fetch "$TERMUX/pool/main/libt/libtalloc/libtalloc_2.5.0_aarch64.deb" talloc.deb

STAGE="$CACHE/proot"
if [ ! -d "$STAGE" ]; then
  mkdir -p "$STAGE"
  for deb in proot shmem talloc; do
    mkdir -p "$CACHE/$deb"
    # bsdtar reads the ar container that a .deb is; GNU tar needs ar first.
    if tar -xf "$CACHE/$deb.deb" -C "$CACHE/$deb" 2>/dev/null; then
      tar -xf "$CACHE/$deb"/data.tar.* -C "$STAGE"
    else
      (cd "$CACHE/$deb" && ar x "$CACHE/$deb.deb" && tar -xf data.tar.* -C "$STAGE")
    fi
  done
fi
PREFIX="$STAGE/data/data/com.termux/files/usr"

cp "$PREFIX/bin/proot" "$MAIN/jniLibs/arm64-v8a/libproot.so"
cp "$PREFIX/libexec/proot/loader" "$MAIN/jniLibs/arm64-v8a/libproot_loader.so"
cp "$PREFIX/lib/libtalloc.so.2.5.0" "$MAIN/jniLibs/arm64-v8a/libtalloc.so"
cp "$PREFIX/lib/libandroid-shmem.so" "$MAIN/jniLibs/arm64-v8a/libandroid_shmem.so"

# proot is linked against Termux's prefix and sonames that jniLibs cannot use.
# $ORIGIN plus lib-prefixed names put every dependency next to the binary.
if command -v patchelf >/dev/null 2>&1; then
  patchelf --replace-needed libtalloc.so.2 libtalloc.so \
           --replace-needed libandroid-shmem.so libandroid_shmem.so \
           --set-rpath '$ORIGIN' "$MAIN/jniLibs/arm64-v8a/libproot.so"
else
  echo "patchelf is required: brew install patchelf" >&2
  exit 1
fi

echo "== rootfs"
fetch "$UBUNTU/ubuntu-base-24.04.5-base-arm64.tar.gz" ubuntu-base.tar.gz
if [ ! -f "$MAIN/assets/rootfs.bin" ]; then
  # Android denies link() in app data, so dpkg cannot run and the archive must
  # not contain hardlink entries: every one becomes a full copy.
  python3 - "$CACHE/ubuntu-base.tar.gz" "$MAIN/assets/rootfs.bin" <<'PY'
import os
import shutil
import sys
import tarfile
import tempfile

source, target = sys.argv[1], sys.argv[2]
work = tempfile.mkdtemp(prefix="yoreni-rootfs-")
try:
    with tarfile.open(source) as archive:
        archive.extractall(work, filter="fully_trusted")

    def add(handle, path, name):
        info = handle.gettarinfo(path, name)
        info.name = name
        if info.islnk():
            info.type = tarfile.REGTYPE
            info.linkname = ""
            info.size = os.path.getsize(path)
        if info.isreg():
            with open(path, "rb") as data:
                handle.addfile(info, data)
        else:
            handle.addfile(info)

    with tarfile.open(target, "w:gz", compresslevel=6) as handle:
        for entry in sorted(os.listdir(work)):
            add(handle, os.path.join(work, entry), entry)
finally:
    shutil.rmtree(work, ignore_errors=True)
PY
fi

echo "== python"
fetch "https://api.github.com/repos/astral-sh/python-build-standalone/releases/assets/$PYTHON_ASSET" python.tar.gz
cp "$CACHE/python.tar.gz" "$MAIN/assets/python.bin"

echo "== yoreni"
tar czf "$MAIN/assets/yoreni.bin" -C "$ROOT/.." \
  --exclude='__pycache__' --exclude='*.pyc' yoreni pyproject.toml

echo "done: assets and jniLibs staged under $MAIN"
