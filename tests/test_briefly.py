#!/usr/bin/env python3
"""Unit tests for Briefly local-first filing assistant (stdlib only)."""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import briefly


class TestBriefly(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.inbox = Path(self.test_dir) / "inbox"
        self.matters = Path(self.test_dir) / "matters"
        self.unrelated = Path(self.test_dir) / "unrelated"
        self.inbox.mkdir(parents=True)
        self.matters.mkdir(parents=True)
        self.unrelated.mkdir(parents=True)
        self.original_settings = dict(briefly.SETTINGS)
        briefly.SETTINGS["inbox"] = str(self.inbox)
        briefly.SETTINGS["library"] = str(self.matters)
        briefly.SETTINGS["unrelated_folder"] = str(self.unrelated)

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

    def test_is_local_url(self):
        self.assertTrue(briefly.is_local_url("http://127.0.0.1:11434"))
        self.assertTrue(briefly.is_local_url("http://localhost:11434"))
        self.assertTrue(briefly.is_local_url("https://127.0.0.1:8765/api"))
        # Security test: reject attacker domains starting with 127.0.0.1 or localhost
        self.assertFalse(briefly.is_local_url("http://127.0.0.1.attacker.com"))
        self.assertFalse(briefly.is_local_url("http://localhost.evil.com"))
        self.assertFalse(briefly.is_local_url("http://example.com"))
        self.assertFalse(briefly.is_local_url("file:///etc/passwd"))
        self.assertFalse(briefly.is_local_url("not a url"))

    def test_unique_destination_multiple_collisions(self):
        dest_dir = self.matters / "Collisions"
        dest_dir.mkdir(parents=True)
        # Create base file and simulated timestamp collision
        f1 = briefly.unique_destination(dest_dir, "doc.txt")
        self.assertEqual(f1.name, "doc.txt")
        f1.write_text("v1")

        # Second one gets timestamp
        f2 = briefly.unique_destination(dest_dir, "doc.txt")
        self.assertNotEqual(f2.name, "doc.txt")
        self.assertTrue(f2.name.startswith("doc ("))
        f2.write_text("v2")

        # Third one with pre-existing f2 name gets counter suffix
        f3 = briefly.unique_destination(dest_dir, "doc.txt")
        self.assertNotEqual(f3, f1)
        self.assertNotEqual(f3, f2)
        f3.write_text("v3")

        self.assertEqual(f1.read_text(), "v1")
        self.assertEqual(f2.read_text(), "v2")
        self.assertEqual(f3.read_text(), "v3")

    def test_classify_non_dict_response(self):
        (self.matters / "Matter Alpha").mkdir()
        test_file = self.inbox / "file.txt"
        test_file.write_text("Some text")

        # Mock Ollama returning a JSON array instead of dict
        mock_response = {"response": json.dumps(["not", "a", "dict"])}
        with patch("urllib.request.urlopen") as mock_url:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = json.dumps(mock_response).encode()
            mock_url.return_value = mock_cm

            with self.assertRaises(RuntimeError) as ctx:
                briefly.classify(test_file, "Some text")
            self.assertIn("unreadable result", str(ctx.exception))

    def test_pick_folder_subprocess(self):
        # 1. Successful selection via subprocess (e.g. zenity)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "/custom/path/chosen\n"
        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/zenity" if cmd == "zenity" else None):
            with patch("subprocess.run", return_value=mock_proc):
                self.assertEqual(briefly.pick_folder(), "/custom/path/chosen")

        # 2. Cancelled selection via subprocess (returncode 1)
        mock_cancel = MagicMock()
        mock_cancel.returncode = 1
        mock_cancel.stdout = ""
        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/zenity" if cmd == "zenity" else None):
            with patch("subprocess.run", return_value=mock_cancel):
                self.assertEqual(briefly.pick_folder(), "")

    def test_api_exclude_success(self):
        server = briefly.ThreadingHTTPServer(("127.0.0.1", 0), briefly.Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            # Create a receipt file in the inbox
            test_file = self.inbox / "receipt_123.txt"
            test_file.write_text("Office chair receipt $129.00")

            req_body = json.dumps({"source": str(test_file)}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/exclude", data=req_body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read())
                self.assertTrue(data.get("ok"))

            # File is removed from inbox and placed into unrelated folder
            self.assertFalse(test_file.exists())
            dest_file = self.unrelated / "receipt_123.txt"
            self.assertTrue(dest_file.exists())
            self.assertEqual(dest_file.read_text(), "Office chair receipt $129.00")

            # Check audit event
            with briefly.db() as con:
                rows = [dict(r) for r in con.execute("SELECT * FROM activity WHERE filename='receipt_123.txt'")]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["status"], "excluded")
                self.assertIn("Moved out of inbox", rows[0]["reason"])
        finally:
            server.shutdown()
            server.server_close()

    def test_api_exclude_collision(self):
        server = briefly.ThreadingHTTPServer(("127.0.0.1", 0), briefly.Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            # Pre-existing file in unrelated folder
            existing = self.unrelated / "ticket.txt"
            existing.write_text("Original ticket copy")

            # Incoming file with same name in inbox
            incoming = self.inbox / "ticket.txt"
            incoming.write_text("New ticket copy")

            req_body = json.dumps({"source": str(incoming)}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/exclude", data=req_body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)

            # Original file untouched
            self.assertEqual(existing.read_text(), "Original ticket copy")
            # New file moved with unique timestamp suffix
            collision_copies = list(self.unrelated.glob("ticket (*).txt"))
            self.assertEqual(len(collision_copies), 1)
            self.assertEqual(collision_copies[0].read_text(), "New ticket copy")
        finally:
            server.shutdown()
            server.server_close()

    def test_api_exclude_outside_inbox_rejected(self):
        server = briefly.ThreadingHTTPServer(("127.0.0.1", 0), briefly.Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            # File outside inbox
            outside = Path(self.test_dir) / "outside.txt"
            outside.write_text("Secret outside file")

            req_body = json.dumps({"source": str(outside)}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/exclude", data=req_body,
                                         headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 400)
            ctx.exception.close()
            self.assertTrue(outside.exists())
        finally:
            server.shutdown()
            server.server_close()

    def test_api_settings_unrelated_folder(self):
        server = briefly.ThreadingHTTPServer(("127.0.0.1", 0), briefly.Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            new_unrelated = Path(self.test_dir) / "custom_unrelated"
            req_body = json.dumps({"unrelated_folder": str(new_unrelated)}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/settings", data=req_body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read())
                self.assertEqual(data["state"]["settings"]["unrelated_folder"], str(new_unrelated))
            self.assertTrue(new_unrelated.exists())
        finally:
            server.shutdown()
            server.server_close()


    def test_discover_matters_empty_inbox(self):
        res = briefly.discover_matters()
        self.assertEqual(res["suggestions"], [])
        self.assertEqual(res["skipped_cooldown"], [])

    def test_discover_matters_with_files(self):
        doc = self.inbox / "contract.txt"
        doc.write_text("Agreement between Acme Corp and Beta LLC.")
        mock_result = {
            "matter": "Acme Corp Merger",
            "document_type": "Agreement",
            "confidence": 0.92,
            "reason": "Strong evidence of merger agreement"
        }
        with patch("briefly.classify", return_value=mock_result):
            res = briefly.discover_matters()
            self.assertEqual(len(res["suggestions"]), 1)
            self.assertEqual(res["suggestions"][0]["matter"], "Acme Corp Merger")
            self.assertEqual(res["suggestions"][0]["filename"], "contract.txt")
            self.assertEqual(res["suggestions"][0]["confidence"], 0.92)

    def test_api_discover(self):
        server = briefly.ThreadingHTTPServer(("127.0.0.1", 0), briefly.Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/discover", data=b"{}",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read())
                self.assertIn("suggestions", data)
                self.assertIn("skipped_cooldown", data)
        finally:
            server.shutdown()
            server.server_close()


    def test_canonical_matter_key(self):
        self.assertEqual(briefly.canonical_matter_key("Smith v. Jones"), "smith v jones")
        self.assertEqual(briefly.canonical_matter_key("Smith_vs_Jones"), "smith v jones")
        self.assertEqual(briefly.canonical_matter_key("Smith vs. Jones"), "smith v jones")
        self.assertEqual(briefly.canonical_matter_key("Smith versus Jones"), "smith v jones")
        self.assertEqual(briefly.canonical_matter_key("Smith v Jones"), "smith v jones")
        self.assertEqual(briefly.canonical_matter_key("Estate of Joseph Ramdial."), "estate of joseph ramdial")

    def test_classify_canonical_reconciliation(self):
        (self.matters / "Garcia v Northstar Ltd").mkdir()
        test_file = self.inbox / "order.txt"
        test_file.write_text("Court order content")
        # Model returns punctuation variation "Garcia v. Northstar Ltd"
        mock_response = {
            "response": json.dumps({
                "matter": "Garcia v. Northstar Ltd",
                "document_type": "Court Orders",
                "confidence": 0.95,
                "reason": "Clear order"
            })
        }
        with patch("urllib.request.urlopen") as mock_url:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = json.dumps(mock_response).encode()
            mock_url.return_value = mock_cm

            res = briefly.classify(test_file, "Court order content")
            # Should reconcile to the exact existing directory name!
            self.assertEqual(res["matter"], "Garcia v Northstar Ltd")

    def test_classify_closed_world_hallucination_stripped(self):
        (self.matters / "Garcia v Northstar Ltd").mkdir()
        test_file = self.inbox / "doc.txt"
        test_file.write_text("Some text")
        # Model returns an invented matter not in the library
        mock_response = {
            "response": json.dumps({
                "matter": "Completely Hallucinated Matter",
                "document_type": "Other",
                "confidence": 0.9,
                "reason": "Invented name"
            })
        }
        with patch("urllib.request.urlopen") as mock_url:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = json.dumps(mock_response).encode()
            mock_url.return_value = mock_cm

            res = briefly.classify(test_file, "Some text", allow_unlisted=False)
            self.assertIsNone(res["matter"])

    def test_create_matter_folder_canonical_duplicate_rejected(self):
        (self.matters / "Smith v Jones").mkdir()
        with self.assertRaises(ValueError) as ctx:
            briefly.create_matter_folder("Smith v. Jones")
        self.assertIn("already exists", str(ctx.exception))

    def test_extract_text_scanned_pdf(self):
        test_pdf = self.inbox / "scanned.pdf"
        test_pdf.write_bytes(b"%PDF-1.4 dummy")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "   \n\n  12   \n "  # Fewer than 40 alphanumeric characters
        with patch("shutil.which", return_value="/usr/bin/pdftotext"):
            with patch("subprocess.run", return_value=mock_proc):
                with self.assertRaises(RuntimeError) as ctx:
                    briefly.extract_text(test_pdf)
                self.assertIn("scanned image without OCR text", str(ctx.exception))

    def test_extract_text_password_protected_pdf(self):
        test_pdf = self.inbox / "locked.pdf"
        test_pdf.write_bytes(b"%PDF-1.4 dummy")
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "Command Line Error: Incorrect password"
        with patch("shutil.which", return_value="/usr/bin/pdftotext"):
            with patch("subprocess.run", return_value=mock_proc):
                with self.assertRaises(RuntimeError) as ctx:
                    briefly.extract_text(test_pdf)
                self.assertIn("Password-protected PDF", str(ctx.exception))

    def test_extract_text_corrupt_docx(self):
        test_docx = self.inbox / "bad.docx"
        test_docx.write_text("Not a real zip archive")
        with self.assertRaises(RuntimeError) as ctx:
            briefly.extract_text(test_docx)
        self.assertIn("Corrupted or encrypted DOCX", str(ctx.exception))

    def test_extract_text_oversized_file(self):
        test_file = self.inbox / "huge.txt"
        test_file.write_text("Short text")
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 55 * 1024 * 1024
            with self.assertRaises(RuntimeError) as ctx:
                briefly.extract_text(test_file)
            self.assertIn("exceeds 50 MB safety limit", str(ctx.exception))

    def test_extract_text_zip_archive(self):
        test_zip = self.inbox / "bundle.zip"
        test_zip.write_bytes(b"PK\x03\x04")
        with self.assertRaises(RuntimeError) as ctx:
            briefly.extract_text(test_zip)
        self.assertIn("Archive bundle (.zip)", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
