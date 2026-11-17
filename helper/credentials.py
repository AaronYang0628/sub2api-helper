"""Encrypt connection credentials at rest, using a separate local master key.

The database and its adjacent ``.key`` file together grant access to the saved
credential. Protect and back up both files. Losing the key is intentionally not
handled by generating a replacement: that would hide an unrecoverable binding.
"""

import json
import os
from pathlib import Path
import stat
import threading

from cryptography.fernet import Fernet, InvalidToken


_SETTING = "connection_sealed"
_ERROR = "Saved connection credentials are unavailable; restore the original vault key and database together"


class CredentialVault:
    def __init__(self, store, db_path):
        self.store = store
        self.key_path = None if db_path == ":memory:" else Path(str(db_path) + ".key")
        self._ephemeral_key = Fernet.generate_key() if self.key_path is None else None
        self._lock = threading.RLock()

    def _read_masterfile(self):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(self.key_path, flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("Invalid vault key file")
            if info.st_uid != os.geteuid():
                raise ValueError("Invalid vault key owner")
            key = os.read(fd, 128).strip()
            if len(key) != 44:
                raise ValueError("Invalid vault key")
            return key
        finally:
            os.close(fd)

    def _create_masterfile(self):
        key = Fernet.generate_key()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(self.key_path, flags, 0o600)
        except FileExistsError:
            # Another writer won creation. Read its key, never overwrite it.
            return self._read_masterfile()
        try:
            os.fchmod(fd, 0o600)
            remaining = memoryview(key)
            while remaining:
                count = os.write(fd, remaining)
                if count <= 0:
                    raise OSError("Vault key write failed")
                remaining = remaining[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        # Flush creation before any durable ciphertext can reference this key.
        directory_fd = os.open(self.key_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return key

    def _cipher(self, allow_create=False):
        if self._ephemeral_key is not None:
            return Fernet(self._ephemeral_key)
        try:
            key = self._read_masterfile()
        except FileNotFoundError:
            if not allow_create:
                raise RuntimeError(_ERROR) from None
            key = self._create_masterfile()
        return Fernet(key)

    def read(self):
        with self._lock:
            sealed = self.store.get_setting(_SETTING)
            if sealed is None:
                return None
            try:
                if not isinstance(sealed, str):
                    raise ValueError("Invalid sealed connection")
                payload = self._cipher().decrypt(sealed.encode("ascii"))
                value = json.loads(payload)
                if (not isinstance(value, dict) or set(value) != {"url", "admin_key"}
                        or not isinstance(value["url"], str) or not value["url"]
                        or not isinstance(value["admin_key"], str) or not value["admin_key"]):
                    raise ValueError("Invalid sealed connection")
                return value
            except (OSError, ValueError, TypeError, UnicodeError, InvalidToken, RuntimeError):
                raise RuntimeError(_ERROR) from None

    def write(self, url, admin_key):
        if not isinstance(url, str) or not url or not isinstance(admin_key, str) or not admin_key:
            raise ValueError("Connection URL and administrator key are required")
        with self._lock:
            try:
                existing = self.store.get_setting(_SETTING)
                cipher = self._cipher(allow_create=existing is None)
                if existing is not None:
                    # Validate the existing binding before replacing it, even if
                    # its master file was replaced with a different valid key.
                    self.read()
                sealed = cipher.encrypt(json.dumps({"url": url, "admin_key": admin_key}, separators=(",", ":")).encode("utf-8"))
                self.store.set_setting(_SETTING, sealed.decode("ascii"))
            except (OSError, ValueError, TypeError, UnicodeError, InvalidToken, RuntimeError):
                raise RuntimeError(_ERROR) from None
