import os
import shutil
import unittest
from uuid import uuid4
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import storage
from routers import pro as pro_module


WORKSPACE_TMP = storage.BASE_DIR / "tmp"


class PersistentStoragePathTests(unittest.TestCase):
    def make_workspace_temp(self) -> Path:
        root = WORKSPACE_TMP / "storage_tests" / uuid4().hex
        root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, root, True)
        return root

    def test_local_fallback_paths_are_used_without_railway_volume(self):
        with patch.dict(os.environ, {storage.RAILWAY_VOLUME_ENV: ""}, clear=False):
            paths = storage.configured_storage_paths()

        self.assertFalse(paths.using_railway_volume)
        self.assertEqual(paths.visual_reference_uploads_dir, storage.STATIC_DIR / "visual-references" / "uploads")
        self.assertEqual(paths.estimate_pdfs_dir, storage.STATE_DIR / "estimate_pdfs")

    def test_railway_mount_paths_are_used_and_directories_are_created(self):
        root = self.make_workspace_temp() / "persistent"
        with patch.dict(os.environ, {storage.RAILWAY_VOLUME_ENV: str(root)}, clear=False):
            paths = storage.ensure_storage_directories()

        self.assertTrue(paths.using_railway_volume)
        self.assertEqual(paths.root, root)
        self.assertEqual(paths.uploads_dir, root / "uploads")
        self.assertEqual(paths.pdfs_dir, root / "pdfs")
        self.assertEqual(paths.visual_reference_uploads_dir, root / "uploads" / "visual-references")
        self.assertEqual(paths.estimate_pdfs_dir, root / "pdfs" / "estimates")
        self.assertTrue(paths.uploads_dir.is_dir())
        self.assertTrue(paths.pdfs_dir.is_dir())
        self.assertTrue(paths.visual_reference_uploads_dir.is_dir())
        self.assertTrue(paths.estimate_pdfs_dir.is_dir())

    def test_upload_write_uses_railway_mount_without_changing_public_url(self):
        root = self.make_workspace_temp() / "persistent"
        with patch.dict(os.environ, {storage.RAILWAY_VOLUME_ENV: str(root)}, clear=False):
            url = pro_module.save_visual_reference_upload(
                {"filename": "finding-photo.JPG", "content_type": "image/jpeg", "content": b"photo"}
            )
            paths = storage.configured_storage_paths()

        self.assertRegex(url, r"^/static/visual-references/uploads/[a-f0-9]{32}\.jpg$")
        stored_name = url.rsplit("/", 1)[-1]
        self.assertEqual((paths.visual_reference_uploads_dir / stored_name).read_bytes(), b"photo")

    def test_existing_static_upload_url_serves_railway_volume_file(self):
        root = self.make_workspace_temp() / "persistent"
        filename = "served-photo.jpg"
        with patch.dict(os.environ, {storage.RAILWAY_VOLUME_ENV: str(root)}, clear=False):
            paths = storage.ensure_storage_directories()
            (paths.visual_reference_uploads_dir / filename).write_bytes(b"image-bytes")
            import main

            response = TestClient(main.app, base_url="http://localhost").get(
                f"/static/visual-references/uploads/{filename}"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"image-bytes")

    def test_filenames_cannot_escape_storage_directory(self):
        root = self.make_workspace_temp()
        with self.assertRaises(HTTPException):
            storage.resolve_storage_child(root, "../escape.pdf")
        with self.assertRaises(HTTPException):
            storage.resolve_storage_child(root, "nested/escape.pdf")
        with self.assertRaises(HTTPException):
            storage.safe_upload_suffix("../escape.jpg")
        with self.assertRaises(HTTPException):
            storage.safe_upload_suffix("nested\\escape.jpg")


if __name__ == "__main__":
    unittest.main()
