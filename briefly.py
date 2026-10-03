#!/usr/bin/env python3
"""Briefly: local-first legal document filing assistant (stdlib-only MVP)."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

APP = Path(__file__).resolve().parent
WORK = APP / "workspace"
DEFAULTS = {
    "inbox": str(WORK / "inbox"), "library": str(WORK / "matters"),
    "cooldown_seconds": 0, "extensions": [".pdf", ".docx", ".txt", ".md"],
    "excluded_extensions": [".zip", ".exe", ".jpg", ".jpeg", ".png"],
    "confidence_threshold": 0.82, "watch_enabled": False,
    "watch_interval_seconds": 15, "ollama_url": "http://127.0.0.1:11434",
    "model": "gemma4:e2b-it-qat",
}
LOCK = threading.RLock()
CONFIG = APP / "workspace" / "settings.json"
DB = APP / "workspace" / "briefly.sqlite3"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_settings():
    WORK.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        try:
            saved = json.loads(CONFIG.read_text())
            return {**DEFAULTS, **saved}
        except (ValueError, OSError):
            pass
    return dict(DEFAULTS)


SETTINGS = load_settings()


def ensure_workspace_settings():
    """Ensure inbox and library paths are configured and valid."""
    for key, default in (("inbox", WORK / "inbox"), ("library", WORK / "matters")):
        value = str(SETTINGS.get(key, "")).strip()
        if not value:
            SETTINGS[key] = str(default.resolve())
    save_settings()


def save_settings():
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(SETTINGS, indent=2))


ensure_workspace_settings()


def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS activity (
      id INTEGER PRIMARY KEY, created_at TEXT, filename TEXT, source TEXT,
      destination TEXT, status TEXT, doc_type TEXT, matter TEXT,
      confidence REAL, reason TEXT)""")
    con.commit()
    return con


def log_activity(filename, source, destination, status, doc_type="", matter="", confidence=0, reason=""):
    with db() as con:
        con.execute("INSERT INTO activity(created_at,filename,source,destination,status,doc_type,matter,confidence,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                    (now(), filename, str(source), str(destination or ""), status, doc_type, matter, confidence, reason))


def safe_path(raw: str) -> Path:
    p = Path(os.path.expanduser(raw)).resolve()
    return p


def matter_folders():
    root = safe_path(SETTINGS["library"])
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".txt", ".md"):
        return path.read_text(errors="replace")[:24000]
    if ext == ".docx":
        with zipfile.ZipFile(path) as zf:
            data = zf.read("word/document.xml")
        root = ElementTree.fromstring(data)
        return " ".join(t.text or "" for t in root.iter() if t.tag.endswith("}t"))[:24000]
    if ext == ".pdf":
        pdftotext = shutil.which("pdftotext")
        if not pdftotext:
            raise RuntimeError("PDF text extraction needs pdftotext. Install Poppler, or use DOCX/TXT for this demo.")
        proc = subprocess.run([pdftotext, "-f", "1", "-l", "8", "-layout", str(path), "-"],
                              capture_output=True, text=True, timeout=20)
        if proc.returncode:
            raise RuntimeError("Could not read this PDF")
        text = proc.stdout.strip()
        if not text:
            raise RuntimeError("This PDF appears to be scanned; OCR is not included in this MVP")
        return text[:24000]
    raise RuntimeError("Unsupported file type")


def classify(path: Path, text: str, allow_unlisted=False):
    matters = matter_folders()
    if not matters and not allow_unlisted:
        raise RuntimeError("Add at least one matter folder inside the configured library")
    instruction = (f"Identify a likely named legal matter from the document. Suggest a concise, reusable folder name "
                   "(for example, ‘Garcia v Northstar Ltd’ or ‘Estate of Joseph Ramdial’). If the document does not "
                   "clearly identify a legal matter, use null. This is only a proposal; do not assume it is correct.") if allow_unlisted else f"Choose a matter ONLY from this exact list: {json.dumps(matters)}. If none is clearly supported, set matter to null."
    prompt = f'''You are Briefly, a cautious legal document filing assistant. Analyze the supplied document excerpt.
{instruction}
Choose a short, filesystem-safe document_type (e.g. Pleadings, Court Orders, Correspondence, Evidence, Billing, Other).
Return only JSON matching: {{"matter": string|null, "document_type": string, "confidence": number from 0 to 1, "reason": short explanation}}.
Confidence must reflect evidence in the document, not filename alone. Do not infer a client/matter from unrelated text.
Filename: {path.name}\nDocument excerpt:\n{text[:12000]}'''
    body = json.dumps({"model": SETTINGS["model"], "prompt": prompt, "stream": False,
                       "options": {"num_ctx": 4096, "num_predict": 512, "temperature": 0.1},
                       "format": {"type": "object", "properties": {
                           "matter": {"type": ["string", "null"]}, "document_type": {"type": "string"},
                           "confidence": {"type": "number"}, "reason": {"type": "string"}},
                           "required": ["matter", "document_type", "confidence", "reason"]}}).encode()
    req = urllib.request.Request(SETTINGS["ollama_url"].rstrip("/") + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read())
        answer = json.loads(result.get("response", "{}"))
    except urllib.error.URLError as e:
        raise RuntimeError("Cannot reach local Ollama. Start Ollama and confirm the selected model is installed.") from e
    except (ValueError, KeyError) as e:
        raise RuntimeError("The model returned an unreadable result; file left untouched") from e
    matter = answer.get("matter")
    if str(matter).strip().lower() in ("null", "none", ""):
        matter = None
    if not allow_unlisted and matter not in matters:
        matter = None
    if allow_unlisted and matter is not None:
        matter = re.sub(r"[/\\]+", " ", str(matter)).strip()[:100] or None
    dtype = re.sub(r"[^\w -]", "", str(answer.get("document_type", "Other"))).strip()[:48] or "Other"
    try:
        confidence = max(0.0, min(1.0, float(answer.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"matter": matter, "document_type": dtype, "confidence": confidence,
            "reason": str(answer.get("reason", ""))[:300]}


def process_file(path: Path, bypass_cooldown=False):
    inbox = safe_path(SETTINGS["inbox"])
    try:
        path = path.resolve(strict=True)
        path.relative_to(inbox)
    except (ValueError, OSError):
        return None
    if not path.is_file() or path.name.startswith("."):
        return None
    ext = path.suffix.lower()
    allowed = {x.lower() if x.startswith(".") else "." + x.lower() for x in SETTINGS["extensions"]}
    excluded = {x.lower() if x.startswith(".") else "." + x.lower() for x in SETTINGS["excluded_extensions"]}
    if ext in excluded or ext not in allowed:
        return None
    try:
        if time.time() - path.stat().st_mtime < int(SETTINGS["cooldown_seconds"]) and not bypass_cooldown:
            return None
        size1 = path.stat().st_size
        time.sleep(.15)
        if path.stat().st_size != size1:
            return None
        text = extract_text(path)
        result = classify(path, text)
        threshold = float(SETTINGS["confidence_threshold"])
        if result["matter"] and result["confidence"] >= threshold:
            root = safe_path(SETTINGS["library"])
            matter = (root / result["matter"]).resolve()
            matter.relative_to(root)
            destination_dir = (matter / result["document_type"]).resolve()
            destination_dir.relative_to(matter)
            destination_dir.mkdir(exist_ok=True)
            dest = destination_dir / path.name
            if dest.exists():
                dest = destination_dir / f"{path.stem} ({datetime.now().strftime('%Y%m%d-%H%M%S')}){path.suffix}"
            shutil.move(str(path), str(dest))
            log_activity(path.name, path, dest, "filed", result["document_type"], result["matter"], result["confidence"], result["reason"])
            return {"status": "filed", "filename": path.name, "destination": str(dest), **result}
        reason = result["reason"] or ("No existing matter matched" if not result["matter"] else "Below auto-file confidence threshold")
        log_activity(path.name, path, "", "review", result["document_type"], result["matter"] or "", result["confidence"], reason)
        return {"status": "review", "filename": path.name, "destination": "", **result, "reason": reason}
    except Exception as e:
        log_activity(path.name, path, "", "review", reason=str(e)[:300])
        return {"status": "review", "filename": path.name, "destination": "", "reason": str(e)}


def scan(bypass_cooldown=False):
    inbox = safe_path(SETTINGS["inbox"])
    inbox.mkdir(parents=True, exist_ok=True)
    if not matter_folders():
        raise RuntimeError("Your matter library is empty. Run First pass to suggest matter folders, then approve the ones you want.")
    outcomes = []
    with LOCK:
        for p in sorted(inbox.iterdir()):
            if p.is_file():
                result = process_file(p, bypass_cooldown)
                if result:
                    outcomes.append(result)
    return outcomes


def discover_matters():
    """Suggest matter folder names without moving or creating anything."""
    inbox = safe_path(SETTINGS["inbox"])
    inbox.mkdir(parents=True, exist_ok=True)
    allowed = {x.lower() if x.startswith(".") else "." + x.lower() for x in SETTINGS["extensions"]}
    excluded = {x.lower() if x.startswith(".") else "." + x.lower() for x in SETTINGS["excluded_extensions"]}
    suggestions, skipped_cooldown = [], []
    with LOCK:
        for path in sorted(inbox.iterdir()):
            if not path.is_file() or path.suffix.lower() not in allowed or path.suffix.lower() in excluded:
                continue
            try:
                size = path.stat().st_size
                if time.time() - path.stat().st_mtime < int(SETTINGS["cooldown_seconds"]):
                    skipped_cooldown.append(path.name)
                    continue
                time.sleep(.15)
                if path.stat().st_size != size:
                    skipped_cooldown.append(path.name)
                    continue
            except OSError:
                continue
            try:
                text = extract_text(path)
                result = classify(path, text, allow_unlisted=True)
                suggestions.append({"filename": path.name, "source": str(path), **result})
            except Exception as e:
                suggestions.append({"filename": path.name, "source": str(path), "matter": None,
                                   "document_type": "Other", "confidence": 0, "reason": str(e)[:300]})
    return {"suggestions": suggestions, "skipped_cooldown": skipped_cooldown}


def pick_folder(initial=""):
    """Use the OS directory chooser so the server receives an absolute local path."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(initialdir=initial if Path(initial).is_dir() else str(Path.home()),
                                           title="Choose a local folder", mustexist=False)
        root.destroy()
        return selected
    except Exception as e:
        raise RuntimeError(f"Could not open the system folder picker: {e}") from e


def create_matter_folder(proposed):
    name = re.sub(r"[/\\]+", " ", str(proposed))
    name = re.sub(r"[^\w &(),.\'-]", "", name).strip(" .")[:100]
    if not name or name in (".", ".."):
        raise ValueError("Enter a valid matter folder name")
    root = safe_path(SETTINGS["library"])
    root.mkdir(parents=True, exist_ok=True)
    target = (root / name).resolve()
    target.relative_to(root)
    if target.exists():
        raise ValueError("That matter folder already exists")
    target.mkdir()
    log_activity(name, "", target, "matter_created", "", name, 1.0, "Matter folder created after user approval; no files moved")
    return name


def dashboard_data():
    inbox = safe_path(SETTINGS["inbox"])
    pending_files = [p.name for p in inbox.iterdir() if p.is_file()] if inbox.exists() else []
    with db() as con:
        events = [dict(r) for r in con.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 100")]
    return {"settings": SETTINGS, "matters": matter_folders(), "inbox_files": pending_files,
            "events": events, "ollama": ollama_status()}


def ollama_status():
    try:
        req = urllib.request.Request(SETTINGS["ollama_url"].rstrip("/") + "/api/tags")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            tags = json.loads(resp.read()).get("models", [])
        names = [m.get("name", "") for m in tags]
        return {"online": True, "models": names, "selected_available": any(n == SETTINGS["model"] or n.startswith(SETTINGS["model"] + "-") for n in names)}
    except Exception:
        return {"online": False, "models": [], "selected_available": False}


def init_demo():
    inbox = safe_path(SETTINGS["inbox"])
    library = safe_path(SETTINGS["library"])
    inbox.mkdir(parents=True, exist_ok=True)
    library.mkdir(parents=True, exist_ok=True)
    (library / "Garcia v Northstar Ltd").mkdir(exist_ok=True)
    (library / "Ramdial Estate").mkdir(exist_ok=True)
    samples = {
      "WhatsApp Document.txt": "IN THE HIGH COURT OF JUSTICE\nClaim No. CV2026-01234\nBETWEEN: MARIA GARCIA Claimant and NORTHSTAR LIMITED Defendant\nORDER: The defendant shall file its defence within 28 days.",
      "scan_0041.txt": "Dear Counsel, Re: Estate of Joseph Ramdial. Please find enclosed the valuation report for the residential property at 18 Cedar Grove. Kindly advise on next steps.",
      "download (3).txt": "Receipt for office chair. Total: $1,299.00. Thank you for your purchase.",
    }
    for name, content in samples.items():
        p = inbox / name
        if not p.exists():
            p.write_text(content)
            os.utime(p, (time.time() - 120, time.time() - 120))


def init_workspace():
    """Create the user's chosen roots without inventing matters or adding sample files."""
    safe_path(SETTINGS["inbox"]).mkdir(parents=True, exist_ok=True)
    safe_path(SETTINGS["library"]).mkdir(parents=True, exist_ok=True)
    db()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/api/state":
            self.send_json(dashboard_data())
        elif route == "/":
            raw = (APP / "static" / "index.html").read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        global SETTINGS
        route = urlparse(self.path).path
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")) or 0) or b"{}")
            if route == "/api/settings":
                with LOCK:
                    proposed = {**SETTINGS, **data}
                    proposed["inbox"] = str(safe_path(proposed["inbox"]))
                    proposed["library"] = str(safe_path(proposed["library"]))
                    proposed["cooldown_seconds"] = max(0, min(86400, int(proposed["cooldown_seconds"])))
                    proposed["watch_interval_seconds"] = max(5, min(3600, int(proposed["watch_interval_seconds"])))
                    proposed["confidence_threshold"] = max(0.0, min(1.0, float(proposed["confidence_threshold"])))
                    for key in ("extensions", "excluded_extensions"):
                        if not isinstance(proposed[key], list): raise ValueError(f"{key} must be a list")
                        proposed[key] = sorted(set(str(x).lower() if str(x).startswith(".") else "." + str(x).lower() for x in proposed[key]))
                    if not str(proposed["ollama_url"]).startswith("http://127.0.0.1") and not str(proposed["ollama_url"]).startswith("http://localhost"):
                        raise ValueError("For privacy, Ollama URL must point to this computer (localhost)")
                    proposed["model"] = str(proposed["model"])[:100]
                    Path(proposed["inbox"]).mkdir(parents=True, exist_ok=True)
                    Path(proposed["library"]).mkdir(parents=True, exist_ok=True)
                    SETTINGS = proposed; save_settings()
                self.send_json({"ok": True, "state": dashboard_data()})
            elif route == "/api/pick-folder":
                kind = "inbox" if data.get("kind") == "inbox" else "library"
                self.send_json({"path": pick_folder(SETTINGS[kind])})
            elif route == "/api/discover":
                self.send_json(discover_matters())
            elif route == "/api/create-matter":
                name = create_matter_folder(data.get("name", ""))
                self.send_json({"ok": True, "name": name, "state": dashboard_data()})
            elif route == "/api/scan":
                self.send_json({"results": scan()})
            elif route == "/api/demo":
                init_demo(); self.send_json({"ok": True, "state": dashboard_data()})
            elif route == "/api/approve":
                inbox = safe_path(SETTINGS["inbox"]); root = safe_path(SETTINGS["library"])
                source = safe_path(data.get("source", "")); source.relative_to(inbox)
                matter_name = str(data.get("matter", ""))
                if matter_name not in matter_folders(): raise ValueError("Choose an existing matter")
                dtype = re.sub(r"[^\w -]", "", str(data.get("document_type", "Other"))).strip()[:48] or "Other"
                matter = (root / matter_name).resolve(); matter.relative_to(root)
                destination_dir = (matter / dtype).resolve(); destination_dir.relative_to(matter)
                if not source.is_file(): raise ValueError("The file is no longer in the inbox")
                destination_dir.mkdir(exist_ok=True)
                dest = destination_dir / source.name
                if dest.exists(): dest = destination_dir / f"{source.stem} ({datetime.now().strftime('%Y%m%d-%H%M%S')}){source.suffix}"
                shutil.move(str(source), str(dest))
                log_activity(source.name, source, dest, "filed_by_user", dtype, matter_name, 1.0, "Filed from review queue")
                self.send_json({"ok": True, "state": dashboard_data()})
            else:
                self.send_json({"error": "Not found"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 400)


def watcher():
    while True:
        time.sleep(max(5, int(SETTINGS["watch_interval_seconds"])))
        if SETTINGS["watch_enabled"]:
            scan()


def open_browser():
    time.sleep(0.6)
    try:
        webbrowser.open("http://127.0.0.1:8765")
    except Exception:
        pass


if __name__ == "__main__":
    init_workspace()
    threading.Thread(target=watcher, daemon=True).start()
    if os.environ.get("BRIEFLY_NO_BROWSER") != "1" and "--no-browser" not in sys.argv:
        threading.Thread(target=open_browser, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Briefly is running at http://127.0.0.1:8765 (local only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBriefly stopped")
