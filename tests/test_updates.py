"""Release metadata and download checks for the installer updater."""

from __future__ import annotations

from io import BytesIO
import hashlib
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from web_parser import updates


class UpdateTests(TestCase):
    @staticmethod
    def _folder() -> Path:
        folder = Path.cwd() / ".tmp" / f"update-test-{uuid4().hex}"
        folder.mkdir(parents=True)
        return folder

    def setUp(self) -> None:
        self.installer = b"sample-installer"
        self.digest = hashlib.sha256(self.installer).hexdigest()
        self.manifest = {
            "version": "1.2.3",
            "tag_name": "v1.2.3",
            "filename": "WebParserSetup-1.2.3.exe",
            "size": len(self.installer),
            "sha256": self.digest,
        }

    def test_release_check_and_verified_download(self) -> None:
        payload = json.dumps(self.manifest).encode()
        folder = self._folder()
        with patch.object(updates, "urlopen", side_effect=[BytesIO(payload), BytesIO(self.installer)]), \
                patch.object(updates, "mkdtemp", return_value=str(folder)):
            release = updates.latest_release()
            self.assertTrue(updates.is_newer(release, "1.2.2"))
            destination = updates.download_installer(release)
        self.addCleanup(folder.rmdir)
        self.addCleanup(destination.unlink)
        self.assertEqual(destination.read_bytes(), self.installer)

    def test_rejects_release_with_wrong_installer_name(self) -> None:
        self.manifest["filename"] = "other-setup.exe"
        payload = json.dumps(self.manifest).encode()
        with patch.object(updates, "urlopen", return_value=BytesIO(payload)):
            with self.assertRaisesRegex(updates.UpdateError, "имя установщика"):
                updates.latest_release()

    def test_rejects_corrupt_installer(self) -> None:
        release = updates.Release(
            "1.2.3", "v1.2.3", self.manifest["filename"],
            "https://github.com/SidSpears69/parser/releases/download/"
            "v1.2.3/WebParserSetup-1.2.3.exe",
            len(self.installer), self.digest,
        )
        folder = self._folder()
        with patch.object(updates, "urlopen", return_value=BytesIO(b"other-installer!")), \
                patch.object(updates, "mkdtemp", return_value=str(folder)):
            with self.assertRaisesRegex(updates.UpdateError, "Контрольная сумма"):
                updates.download_installer(release)
        self.assertFalse(folder.exists())

    def test_installed_version_uses_packaged_manifest(self) -> None:
        with patch.object(updates.sys, "frozen", True, create=True), \
                patch.object(updates.sys, "executable", str(Path("C:/WebParser/WebParser.exe"))), \
                patch.object(Path, "read_text", return_value='{"version":"2.4.1"}'):
            self.assertEqual(updates.installed_version(), "2.4.1")
