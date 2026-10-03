#!/usr/bin/env python3
"""Unit tests for Briefly local-first filing assistant (stdlib only)."""
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import briefly


class TestBriefly(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.inbox = Path(self.test_dir) / "inbox"
        self.matters = Path(self.test_dir) / "matters"
        self.inbox.mkdir(parents=True)
        self.matters.mkdir(parents=True)
        self.original_settings = dict(briefly.SETTINGS)
        briefly.SETTINGS["inbox"] = str(self.inbox)
        briefly.SETTINGS["library"] = str(self.matters)

    def tearDown(self):
        briefly.SETTINGS.clear()
        briefly.SETTINGS.update(self.original_settings)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_safe_path(self):
        p = briefly.safe_path("~/some_path")
        self.assertTrue(p.is_absolute())
        self.assertEqual(p, Path(os.path.expanduser("~/some_path")).resolve())

    def test_extract_text_txt_and_md(self):
        txt_file = self.inbox / "test.txt"
        txt_file.write_text("Hello legal world", encoding="utf-8")
        self.assertEqual(briefly.extract_text(txt_file), "Hello legal world")

        md_file = self.inbox / "test.md"
        md_file.write_text("# Case Summary\nRelevant details.", encoding="utf-8")
        self.assertEqual(briefly.extract_text(md_file), "# Case Summary\nRelevant details.")

    def test_extract_text_docx(self):
        docx_file = self.inbox / "test.docx"
        # Create minimal valid docx with word/document.xml
        xml_content = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Claim No. 12345 Court Order</w:t></w:r></w:p></w:body>'
            '</w:document>'
        )
        with zipfile.ZipFile(docx_file, "w") as zf:
            zf.writestr("word/document.xml", xml_content)
        extracted = briefly.extract_text(docx_file)
        self.assertEqual(extracted.strip(), "Claim No. 12345 Court Order")

    def test_extract_text_unsupported_type(self):
        png_file = self.inbox / "scan.png"
        png_file.write_bytes(b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(RuntimeError) as ctx:
            briefly.extract_text(png_file)
        self.assertIn("Unsupported file type", str(ctx.exception))

    def test_matter_folders(self):
        (self.matters / "Matter A").mkdir()
        (self.matters / "Matter B").mkdir()
        (self.matters / ".hidden_matter").mkdir()
        folders = briefly.matter_folders()
        self.assertEqual(folders, ["Matter A", "Matter B"])

    def test_create_matter_folder(self):
        name = briefly.create_matter_folder("Smith v Jones (2026)")
        self.assertEqual(name, "Smith v Jones (2026)")
        self.assertTrue((self.matters / "Smith v Jones (2026)").is_dir())

        # Duplicate should fail
        with self.assertRaises(ValueError):
            briefly.create_matter_folder("Smith v Jones (2026)")

        # Invalid or empty name should fail
        with self.assertRaises(ValueError):
            briefly.create_matter_folder("..")

    def test_classify_null_handling(self):
        (self.matters / "Matter Alpha").mkdir()
        test_file = self.inbox / "file.txt"
        test_file.write_text("Receipt content without legal context")

        # Mock Ollama returning JSON with string "null"
        mock_response = {
            "response": json.dumps({
                "matter": "null",
                "document_type": "Other",
                "confidence": 0.1,
                "reason": "Not related to legal matters"
            })
        }
        with patch("urllib.request.urlopen") as mock_url:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = json.dumps(mock_response).encode()
            mock_url.return_value = mock_cm

            result = briefly.classify(test_file, "Receipt content", allow_unlisted=True)
            self.assertIsNone(result["matter"])
            self.assertEqual(result["document_type"], "Other")

    def test_classify_match_existing_matter(self):
        (self.matters / "Alpha Corp v Beta Ltd").mkdir()
        test_file = self.inbox / "motion.txt"
        test_file.write_text("Notice of Motion in Alpha Corp v Beta Ltd")

        mock_response = {
            "response": json.dumps({
                "matter": "Alpha Corp v Beta Ltd",
                "document_type": "Pleadings",
                "confidence": 0.95,
                "reason": "Explicit case title match"
            })
        }
        with patch("urllib.request.urlopen") as mock_url:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = json.dumps(mock_response).encode()
            mock_url.return_value = mock_cm

            result = briefly.classify(test_file, "Notice of Motion", allow_unlisted=False)
            self.assertEqual(result["matter"], "Alpha Corp v Beta Ltd")
            self.assertEqual(result["confidence"], 0.95)

    def test_duplicate_destination_protection(self):
        (self.matters / "Alpha v Beta").mkdir()
        dest_dir = self.matters / "Alpha v Beta" / "Orders"
        dest_dir.mkdir(parents=True)
        (dest_dir / "order.txt").write_text("Original copy")

        test_file = self.inbox / "order.txt"
        test_file.write_text("New incoming copy")

        # Mock classify to return confident match
        with patch("briefly.classify", return_value={
            "matter": "Alpha v Beta",
            "document_type": "Orders",
            "confidence": 0.95,
            "reason": "Matched"
        }):
            res = briefly.process_file(test_file, bypass_cooldown=True)
            self.assertEqual(res["status"], "filed")
            # Original file remains untouched
            self.assertEqual((dest_dir / "order.txt").read_text(), "Original copy")
            # New file was moved with timestamp suffix
            files = list(dest_dir.glob("order (*).txt"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_text(), "New incoming copy")


if __name__ == "__main__":
    unittest.main()
