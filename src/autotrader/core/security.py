"""Local POSIX file protection. Does not defend against root or the same OS user."""

import os
import stat
from pathlib import Path


def private_file(path: Path) -> None:
    """Create/secure an owned regular file; refuse symlinks and special devices."""
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise PermissionError("State file must be regular and owned by this OS user")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def private_database(path: Path) -> None:
    """Protect DB and existing SQLite sidecars before SQLite opens them."""
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise PermissionError("Database directory must be owned and not writable by other users")
    private_file(path)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            private_file(sidecar)
