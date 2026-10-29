#!/usr/bin/env python3
"""Create Immich .immich markers and prove the CIFS open flags Immich uses.

Immich v3.3.5 (storage.repository.ts) does three things for each storage
folder at startup:

  1. createFile  -> fs.writeFile(flag='wx')   O_WRONLY|O_CREAT|O_EXCL
  2. readFile    -> read the marker
  3. overwriteFile -> fs.writeFile(flag='r+') O_RDWR, no truncate

On Samba, a name that starts with '.' is a DOS hidden file. Opening that
file for write can return EACCES after the exclusive create succeeded
(nodejs/node#41093, Immich discussion #12798). The Linux CIFS client
exposes the DOS attribute as a 4-byte little-endian xattr
(user.cifs.dosattrib / user.smb3.dosattrib). Clearing the hidden and
read-only bits leaves a normal archive file, which O_RDWR can open.

The server container runs as root, same as the official Umbrel Immich
image. The mount is uid/gid 1000, file_mode=0660, dir_mode=0770, so the
probe is repeated as uid 1000. Root's DAC override must not be the only
reason the marker looks writable.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Windows file attributes. ARCHIVE is what a normal writable file carries.
FILE_ATTRIBUTE_READONLY = 0x00000001
FILE_ATTRIBUTE_HIDDEN = 0x00000002
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_ARCHIVE = 0x00000020

DOS_XATTRS = ("user.cifs.dosattrib", "user.smb3.dosattrib")

# Files on the Umbrel CIFS mount are uid/gid 1000. chown is a no-op there
# when Unix extensions are off, and it matters on the local ext4 mounts:
# a marker created by root would otherwise be root:root mode 0660, which
# uid 1000 cannot overwrite.
MARKER_UID = 1000


def _timestamp() -> bytes:
    return str(int(time.time() * 1000)).encode("ascii")


def _is_cifs(path: str) -> bool:
    """True when path sits on a cifs/smb mount. User xattrs on ext4 are unrelated."""
    real = os.path.realpath(path if os.path.exists(path) else os.path.dirname(path))
    best = ""
    fstype = ""
    try:
        mounts = open("/proc/mounts", encoding="utf-8", errors="replace")
    except OSError:
        return False
    with mounts:
        for line in mounts:
            parts = line.split()
            if len(parts) < 3:
                continue
            mountpoint = parts[1].replace("\\040", " ")
            if real == mountpoint or real.startswith(mountpoint.rstrip("/") + "/"):
                if len(mountpoint) >= len(best):
                    best = mountpoint
                    fstype = parts[2]
    return fstype in {"cifs", "smb3", "smb2"}


def relax_dos_attributes(path: str) -> bool:
    """Drop hidden and read-only DOS bits. False when the fs is not CIFS."""
    if not _is_cifs(path):
        return False
    changed = False
    for name in DOS_XATTRS:
        current = FILE_ATTRIBUTE_ARCHIVE
        try:
            raw = os.getxattr(path, name)
            if len(raw) >= 4:
                current = int.from_bytes(raw[:4], "little")
        except OSError:
            pass
        updated = (
            current
            & ~(FILE_ATTRIBUTE_READONLY | FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_DIRECTORY)
        ) | FILE_ATTRIBUTE_ARCHIVE
        try:
            os.setxattr(path, name, updated.to_bytes(4, "little"))
        except OSError:
            continue
        changed = True
    return changed


def _create_exclusive(path: str) -> None:
    """Node flag 'wx': create the marker, or keep going if it already exists."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o666)
    except FileExistsError:
        return
    try:
        os.write(fd, _timestamp())
        os.fsync(fd)
    finally:
        os.close(fd)


def _read(path: str) -> None:
    with open(path, "rb") as handle:
        handle.read()


def _overwrite(path: str) -> None:
    """Node flag 'r+': read/write an existing file without truncating it."""
    fd = os.open(path, os.O_RDWR)
    try:
        os.write(fd, _timestamp())
        os.fsync(fd)
    finally:
        os.close(fd)


def probe(path: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    _create_exclusive(path)
    if os.geteuid() == 0:
        try:
            os.chown(path, MARKER_UID, MARKER_UID)
        except OSError:
            pass
    try:
        os.chmod(path, 0o660)
    except OSError:
        # CIFS without Unix extensions accepts chmod and ignores it.
        pass
    _read(path)
    _overwrite(path)


def ensure(path: str) -> None:
    try:
        probe(path)
        return
    except OSError as first_error:
        if not relax_dos_attributes(path):
            raise
        print(
            f"Cleared DOS hidden/read-only on {path} after {first_error.errno}: {first_error}",
            file=sys.stderr,
        )
    probe(path)


def _run_as_uid(uid: int, folders: list[str]) -> int:
    if os.geteuid() == uid:
        return _ensure_all(folders)
    if os.geteuid() != 0:
        print(
            f"Cannot drop to uid {uid} (running as {os.geteuid()}); probing as the current user.",
            file=sys.stderr,
        )
        return _ensure_all(folders)

    # Flush before fork. A block-buffered stdout would otherwise be written
    # twice, once by the child and again by the parent.
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        try:
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)
            code = _ensure_all(folders)
        except Exception as error:  # noqa: BLE001 - child must report and exit
            print(f"uid {uid} probe failed to start: {error}", file=sys.stderr)
            code = 1
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    _pid, status = os.waitpid(pid, 0)
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    return 1


def _ensure_all(folders: list[str]) -> int:
    failed = False
    for folder in folders:
        marker = os.path.join(folder, ".immich")
        try:
            ensure(marker)
        except OSError as error:
            print(
                f"ERROR: .immich marker at {marker} failed for uid {os.geteuid()}: {error}",
                file=sys.stderr,
            )
            failed = True
            continue
        print(f"immich marker ok uid={os.geteuid()} {marker}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", type=int, default=1000, help="Also probe as this uid (default 1000)")
    parser.add_argument("folders", nargs="+", help="Directories that need a .immich marker")
    args = parser.parse_args()
    global MARKER_UID
    MARKER_UID = args.uid

    # The server container runs as root. Also probe as the CIFS uid so a
    # root-only DAC override is not treated as a writable share.
    if os.geteuid() == 0 and args.uid != 0:
        root_status = _ensure_all(args.folders)
        user_status = _run_as_uid(args.uid, args.folders)
        return 1 if root_status or user_status else 0
    return _ensure_all(args.folders)


if __name__ == "__main__":
    sys.exit(main())
