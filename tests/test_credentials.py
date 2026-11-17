from pathlib import Path
import os
import stat
import tempfile
import unittest

from cryptography.fernet import Fernet

from helper.credentials import CredentialVault
from helper.store import Store


class CredentialVaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "helper.db"
        self.store = Store(str(self.path))
        self.vault = CredentialVault(self.store, str(self.path))
        self.url = "https://private-sub2api.example.test"
        self.key = "administrator-credential-never-plaintext"
        self.key_path = Path(str(self.path) + ".key")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_unbound_read_has_no_side_effects(self):
        self.assertIsNone(self.vault.read())
        self.assertFalse(self.key_path.exists())
        self.assertIsNone(self.store.get_setting("connection_sealed"))

    def test_database_only_contains_authenticated_ciphertext_not_plain_credentials(self):
        self.vault.write(self.url, self.key)
        self.assertEqual(self.vault.read(), {"url": self.url, "admin_key": self.key})
        sealed = self.store.get_setting("connection_sealed")
        self.assertIsInstance(sealed, str)
        self.assertNotIn(self.url, sealed)
        self.assertNotIn(self.key, sealed)
        self.assertEqual(stat.S_IMODE(self.key_path.stat().st_mode), 0o600)
        # SQLite's main database and WAL both contain only the sealed payload.
        for file in self.path.parent.glob("helper.db*"):
            raw = file.read_bytes()
            self.assertNotIn(self.key.encode(), raw)
            self.assertNotIn(self.url.encode(), raw)

    def test_restart_reuses_master_key_and_recovers_saved_binding(self):
        self.vault.write(self.url, self.key)
        original = self.key_path.read_bytes()
        self.store.close()
        self.store = Store(str(self.path))
        restored = CredentialVault(self.store, str(self.path))
        self.assertEqual(restored.read(), {"url": self.url, "admin_key": self.key})
        restored.write(self.url, "rotated-credential")
        self.assertEqual(self.key_path.read_bytes(), original)
        self.assertEqual(CredentialVault(self.store, str(self.path)).read()["admin_key"], "rotated-credential")

    def test_missing_master_is_not_recreated_when_binding_exists(self):
        self.vault.write(self.url, self.key)
        sealed = self.store.get_setting("connection_sealed")
        self.key_path.unlink()
        for vault in (self.vault, CredentialVault(self.store, str(self.path))):
            with self.subTest(vault=vault):
                with self.assertRaisesRegex(RuntimeError, "restore the original vault key"):
                    vault.read()
                with self.assertRaises(RuntimeError):
                    vault.write(self.url, "must-not-replace")
                self.assertFalse(self.key_path.exists())
                self.assertEqual(self.store.get_setting("connection_sealed"), sealed)

    def test_corrupt_master_is_preserved_and_error_never_echoes_key(self):
        self.vault.write(self.url, self.key)
        self.key_path.write_bytes(b"corrupt-master-key")
        for operation in (self.vault.read, lambda: self.vault.write(self.url, "new-secret")):
            with self.assertRaises(RuntimeError) as raised:
                operation()
            self.assertNotIn(self.key, str(raised.exception))
            self.assertNotIn("corrupt-master-key", str(raised.exception))
            self.assertEqual(self.key_path.read_bytes(), b"corrupt-master-key")

    def test_bad_preexisting_key_is_never_replaced_for_first_binding(self):
        self.key_path.write_bytes(b"preexisting-bad-key")
        self.key_path.chmod(0o600)
        with self.assertRaises(RuntimeError):
            self.vault.write(self.url, self.key)
        self.assertEqual(self.key_path.read_bytes(), b"preexisting-bad-key")
        self.assertIsNone(self.store.get_setting("connection_sealed"))

    def test_changed_valid_master_cannot_silently_replace_existing_binding(self):
        self.vault.write(self.url, self.key)
        original_sealed = self.store.get_setting("connection_sealed")
        replacement = Fernet.generate_key()
        self.key_path.write_bytes(replacement)
        with self.assertRaises(RuntimeError):
            self.vault.read()
        with self.assertRaises(RuntimeError):
            self.vault.write(self.url, "replacement-secret")
        self.assertEqual(self.store.get_setting("connection_sealed"), original_sealed)
        self.assertEqual(self.key_path.read_bytes(), replacement)

    def test_corrupt_ciphertext_does_not_trigger_key_replacement_or_reset(self):
        self.vault.write(self.url, self.key)
        original_key = self.key_path.read_bytes()
        for corrupt in ("not-a-fernet-token", "", {"unexpected": "object"}):
            with self.subTest(corrupt=corrupt):
                self.store.set_setting("connection_sealed", corrupt)
                with self.assertRaises(RuntimeError):
                    self.vault.read()
                with self.assertRaises(RuntimeError):
                    self.vault.write(self.url, "must-not-replace")
                self.assertEqual(self.store.get_setting("connection_sealed"), corrupt)
                self.assertEqual(self.key_path.read_bytes(), original_key)

    def test_ciphertext_tampering_is_detected(self):
        self.vault.write(self.url, self.key)
        sealed = self.store.get_setting("connection_sealed")
        offset = len(sealed) // 2
        altered = sealed[:offset] + ("B" if sealed[offset] == "A" else "A") + sealed[offset + 1:]
        self.store.set_setting("connection_sealed", altered)
        with self.assertRaises(RuntimeError):
            self.vault.read()

    def test_key_file_with_group_or_other_access_is_rejected_without_chmod(self):
        self.vault.write(self.url, self.key)
        original = self.key_path.read_bytes()
        self.key_path.chmod(0o644)
        with self.assertRaises(RuntimeError):
            self.vault.read()
        with self.assertRaises(RuntimeError):
            self.vault.write(self.url, "must-not-replace")
        self.assertEqual(self.key_path.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(self.key_path.stat().st_mode), 0o644)

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "Symlink protection requires O_NOFOLLOW")
    def test_symlink_key_is_rejected_and_target_preserved(self):
        target = self.path.parent / "unrelated-key"
        original = Fernet.generate_key()
        target.write_bytes(original)
        target.chmod(0o600)
        self.key_path.symlink_to(target)
        with self.assertRaises(RuntimeError):
            self.vault.write(self.url, self.key)
        self.assertEqual(target.read_bytes(), original)
        self.assertTrue(self.key_path.is_symlink())
        self.assertIsNone(self.store.get_setting("connection_sealed"))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO protection requires POSIX")
    def test_fifo_master_path_is_rejected_without_blocking(self):
        os.mkfifo(self.key_path, 0o600)
        with self.assertRaises(RuntimeError):
            self.vault.write(self.url, self.key)
        self.assertTrue(stat.S_ISFIFO(self.key_path.stat().st_mode))
        self.assertIsNone(self.store.get_setting("connection_sealed"))

    def test_memory_vault_uses_no_key_file_and_has_instance_local_lifetime(self):
        memory_store = Store(":memory:")
        try:
            vault = CredentialVault(memory_store, ":memory:")
            vault.write(self.url, self.key)
            self.assertEqual(vault.read(), {"url": self.url, "admin_key": self.key})
            self.assertIsNone(vault.key_path)
            with self.assertRaises(RuntimeError):
                CredentialVault(memory_store, ":memory:").read()
        finally:
            memory_store.close()

    def test_existing_valid_key_is_reused_for_first_binding(self):
        original = Fernet.generate_key()
        self.key_path.write_bytes(original)
        self.key_path.chmod(0o600)
        self.vault.write(self.url, self.key)
        self.assertEqual(self.key_path.read_bytes(), original)
        self.assertEqual(self.vault.read()["admin_key"], self.key)


if __name__ == "__main__":
    unittest.main()
