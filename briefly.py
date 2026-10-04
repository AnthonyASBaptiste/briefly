#!/usr/bin/env python3
"""Briefly: local-first legal document filing assistant (stdlib-only MVP)."""
from __future__ import annotations

import contextlib
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
    "unrelated_folder": str(Path.home() / "Documents"),
    "cooldown_seconds": 0, "extensions": [".pdf", ".docx", ".txt", ".md"],
    "excluded_extensions": [".zip", ".exe", ".jpg", ".jpeg", ".png"],
    "confidence_threshold": 0.82, "watch_enabled": False,
    "watch_interval_seconds": 15, "ollama_url": "http://127.0.0.1:11434",
    "model": "gemma4:e2b-it-qat",
}
LOCK = threading.RLock()
CONFIG = APP / "workspace" / "settings.json"
DB = APP / "workspace" / "briefly.sqlite3"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB safety cap


def canonical_matter_key(name: str) -> str:
    """Normalize a matter name to a canonical comparison key.
    Handles 'Smith v. Jones', 'Smith_vs_Jones', 'Smith versus Jones', 'Smith v Jones'
    """
    if not name:
        return ""
    s = str(name).lower().strip()
    s = re.sub(r"[_\-]+", " ", s)
    s = re.sub(r"\b(vs\.?|versus|v)\b|\bv\.", " v ", s)
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def is_local_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url).strip())
        return parsed.scheme in ("http", "https") and parsed.hostname in ("127.0.0.1", "localhost")
    except Exception:
        return False


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_settings():
    WORK.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        try:
            saved = json.loads(CONFIG.read_text())
            merged = {**DEFAULTS, **saved}
            if not is_local_url(merged.get("ollama_url", "")):
                merged["ollama_url"] = DEFAULTS["ollama_url"]
            return merged
        except (ValueError, OSError):
            pass
    return dict(DEFAULTS)


SETTINGS = load_settings()


def ensure_workspace_settings():
    """Ensure inbox, library, and unrelated_folder paths are configured and valid."""
    for key, default in (("inbox", WORK / "inbox"), ("library", WORK / "matters"), ("unrelated_folder", Path.home() / "Documents")):
        value = str(SETTINGS.get(key, "")).strip()
        if not value or value.startswith("/tmp/"):
            SETTINGS[key] = str(default.resolve())
    save_settings()


def save_settings():
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temp_file = CONFIG.with_suffix(f".tmp.{os.getpid()}")
    try:
        temp_file.write_text(json.dumps(SETTINGS, indent=2))
        temp_file.replace(CONFIG)
    except Exception:
        if temp_file.exists():
            temp_file.unlink(missing_ok=True)
        raise


ensure_workspace_settings()


@contextlib.contextmanager
def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS activity (
          id INTEGER PRIMARY KEY, created_at TEXT, filename TEXT, source TEXT,
          destination TEXT, status TEXT, doc_type TEXT, matter TEXT,
          confidence REAL, reason TEXT)""")
        with con:
            yield con
    finally:
        con.close()


def log_activity(filename, source, destination, status, doc_type="", matter="", confidence=0, reason=""):
    with db() as con:
        con.execute("INSERT INTO activity(created_at,filename,source,destination,status,doc_type,matter,confidence,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                    (now(), filename, str(source), str(destination or ""), status, doc_type, matter, confidence, reason))


def safe_path(raw: str) -> Path:
    p = Path(os.path.expanduser(raw)).resolve()
    return p


def unique_destination(directory: Path, filename: str) -> Path:
    dest = directory / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = directory / f"{stem} ({ts}){suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({ts}_{counter}){suffix}"
        counter += 1
    return candidate


def matter_folders():
    root = safe_path(SETTINGS["library"])
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def extract_text(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_SIZE:
        raise RuntimeError("File exceeds 50 MB safety limit; left for manual review")
    ext = path.suffix.lower()
    if ext == ".zip":
        raise RuntimeError("Archive bundle (.zip); please extract contents into inbox")
    if ext in (".txt", ".md"):
        return path.read_text(errors="replace")[:24000]
    if ext == ".docx":
        try:
            with zipfile.ZipFile(path) as zf:
                if "word/document.xml" not in zf.namelist():
                    raise RuntimeError("Invalid DOCX (missing document.xml)")
                info = zf.getinfo("word/document.xml")
                if info.file_size > MAX_FILE_SIZE:
                    raise RuntimeError("Decompressed DOCX content exceeds safety limit")
                data = zf.read("word/document.xml")
            root = ElementTree.fromstring(data)
            text = " ".join(t.text or "" for t in root.iter() if t.tag.endswith("}t")).strip()
            if not text:
                raise RuntimeError("DOCX contains no extractable text; left for manual review")
            return text[:24000]
        except zipfile.BadZipFile:
            raise RuntimeError("Corrupted or encrypted DOCX file")
        except ElementTree.ParseError:
            raise RuntimeError("Malformed XML in DOCX file")
    if ext == ".pdf":
        pdftotext = shutil.which("pdftotext")
        if not pdftotext:
            raise RuntimeError("PDF text extraction needs pdftotext. Install Poppler, or use DOCX/TXT for this demo.")
        try:
            proc = subprocess.run([pdftotext, "-f", "1", "-l", "8", "-layout", str(path), "-"],
                                  capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            raise RuntimeError("PDF extraction timed out (>15s); left for manual review")
        if proc.returncode:
            err = (proc.stderr or "").lower()
            if "password" in err or "encrypted" in err:
                raise RuntimeError("Password-protected PDF; left for manual review")
            raise RuntimeError("Could not read PDF (damaged or unsupported format)")
        text = proc.stdout.strip()
        alpha_count = sum(c.isalnum() for c in text)
        if alpha_count < 40:
            raise RuntimeError("This PDF appears to be a scanned image without OCR text; left for manual review")
        return text[:24000]
    raise RuntimeError("Unsupported file type")


def classify(path: Path, text: str, allow_unlisted=False):
    matters = matter_folders()
    canonical_map = {canonical_matter_key(m): m for m in matters}
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
        raw_answer = result.get("response", "{}")
        answer = json.loads(raw_answer) if isinstance(raw_answer, str) else raw_answer
        if not isinstance(answer, dict):
            raise ValueError("Expected JSON object from model")
    except urllib.error.URLError as e:
        raise RuntimeError("Cannot reach local Ollama. Start Ollama and confirm the selected model is installed.") from e
    except (ValueError, KeyError, AttributeError, TypeError) as e:
        raise RuntimeError("The model returned an unreadable result; file left untouched") from e
    matter = answer.get("matter")
    if str(matter).strip().lower() in ("null", "none", ""):
        matter = None

    if matter is not None:
        ckey = canonical_matter_key(str(matter))
        if ckey in canonical_map:
            # Reconcile to exact existing directory name on disk
            matter = canonical_map[ckey]
        elif not allow_unlisted:
            # Closed-world constraint: discard any invented/unlisted name
            matter = None
        else:
            matter = re.sub(r"[/\\]+", " ", str(matter)).strip()[:100] or None

    dtype = re.sub(r"[^\w -]", "", str(answer.get("document_type", "Other"))).strip()[:48] or "Other"
    try:
        confidence = max(0.0, min(1.0, float(answer.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"matter": matter, "document_type": dtype, "confidence": confidence,
            "reason": str(answer.get("reason", ""))[:300]}


_PROCESSED_REVIEW_CACHE = {}


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
        st = path.stat()
        cache_key = (str(path), st.st_mtime, st.st_size, tuple(sorted(matter_folders())))
        if not bypass_cooldown and cache_key in _PROCESSED_REVIEW_CACHE:
            return _PROCESSED_REVIEW_CACHE[cache_key]

        if time.time() - st.st_mtime < int(SETTINGS["cooldown_seconds"]) and not bypass_cooldown:
            return None
        size1 = st.st_size
        time.sleep(.15)
        if path.stat().st_size != size1:
            return None
        text = extract_text(path)
        result = classify(path, text)
        threshold = float(SETTINGS["confidence_threshold"])
        if result["matter"] and result["confidence"] >= threshold:
            with LOCK:
                if not path.is_file():
                    return None
                root = safe_path(SETTINGS["library"])
                matter = (root / result["matter"]).resolve()
                matter.relative_to(root)
                destination_dir = (matter / result["document_type"]).resolve()
                destination_dir.relative_to(matter)
                destination_dir.mkdir(exist_ok=True)
                dest = unique_destination(destination_dir, path.name)
                shutil.move(str(path), str(dest))
                log_activity(path.name, path, dest, "filed", result["document_type"], result["matter"], result["confidence"], result["reason"])
                _PROCESSED_REVIEW_CACHE.pop(cache_key, None)
                return {"status": "filed", "filename": path.name, "destination": str(dest), **result}
        reason = result["reason"] or ("No existing matter matched" if not result["matter"] else "Below auto-file confidence threshold")
        log_activity(path.name, path, "", "review", result["document_type"], result["matter"] or "", result["confidence"], reason)
        review_res = {"status": "review", "filename": path.name, "destination": "", **result, "reason": reason}
        _PROCESSED_REVIEW_CACHE[cache_key] = review_res
        return review_res
    except Exception as e:
        log_activity(path.name, path, "", "review", reason=str(e)[:300])
        err_res = {"status": "review", "filename": path.name, "destination": "", "reason": str(e)}
        try:
            st = path.stat()
            _PROCESSED_REVIEW_CACHE[(str(path), st.st_mtime, st.st_size, tuple(sorted(matter_folders())))] = err_res
        except Exception:
            pass
        return err_res


def scan(bypass_cooldown=False):
    inbox = safe_path(SETTINGS["inbox"])
    inbox.mkdir(parents=True, exist_ok=True)
    if not matter_folders():
        raise RuntimeError("Your matter library is empty. Run First pass to suggest matter folders, then approve the ones you want.")
    outcomes = []
    candidates = [p for p in sorted(inbox.iterdir()) if p.is_file()]
    for p in candidates:
        result = process_file(p, bypass_cooldown)
        if result:
            outcomes.append(result)
    return outcomes


def discover_matters():
    """Suggest matter folder names using collective multi-document reasoning across the inbox."""
    inbox = safe_path(SETTINGS["inbox"])
    inbox.mkdir(parents=True, exist_ok=True)
    allowed = {x.lower() if x.startswith(".") else "." + x.lower() for x in SETTINGS["extensions"]}
    excluded = {x.lower() if x.startswith(".") else "." + x.lower() for x in SETTINGS["excluded_extensions"]}
    existing = matter_folders()
    existing_keys = {canonical_matter_key(m): m for m in existing}

    candidates = []
    skipped_cooldown = []
    unreadable = []

    with LOCK:
        for path in sorted(inbox.iterdir()):
            if not path.is_file() or path.suffix.lower() not in allowed or path.suffix.lower() in excluded:
                continue
            try:
                size = path.stat().st_size
                if time.time() - path.stat().st_mtime < int(SETTINGS["cooldown_seconds"]):
                    skipped_cooldown.append(path.name)
                    continue
                time.sleep(.05)
                if path.stat().st_size != size:
                    skipped_cooldown.append(path.name)
                    continue
            except OSError:
                continue
            try:
                text = extract_text(path)
                words = text.split()[:70]
                snippet = " ".join(words)
                candidates.append({"id": len(candidates) + 1, "filename": path.name, "source": str(path), "excerpt": snippet})
            except Exception as e:
                unreadable.append({"filename": path.name, "reason": str(e)[:300]})

    if not candidates:
        return {"suggestions": [], "unrelated_files": [], "skipped_cooldown": skipped_cooldown, "unreadable": unreadable}

    # Global Multi-Document Synthesis Prompt
    doc_index = [{"id": c["id"], "filename": c["filename"], "excerpt": c["excerpt"]} for c in candidates]
    prompt = f"""You are Briefly, an expert legal clerk assistant.
Below is a numbered list of unfiled legal documents in the office inbox.
Analyze all documents TOGETHER to discover the distinct legal matters. Group every document by its document ID.

CRITICAL RULES:
1. Multiple documents often belong to the SAME case (e.g. pleadings, retainer agreements, medical reports, hospital discharge summaries, police accident reports, settlement proposals, and sick leave certificates for the same injured client or accident belong to that ONE litigation case).
2. For litigation/court claims, format the matter folder strictly as "Plaintiff v Defendant" (e.g. "Marcus Sterling v Kevin Gopaul & Colfire" or "Apex Logistics v TrinHaulage Ltd"). Do NOT include claim numbers, damage descriptions, or phrases like "Personal Injury Claim" in the folder name.
3. For probate or estate matters, format strictly as "Estate of [Deceased Name]" (e.g. "Estate of Helena Blackwood").
4. If a document is personal or unrelated office expense (e.g. office supplies receipt), place its document ID in "unrelated_doc_ids".

Existing matter folders in library: {json.dumps(existing)}

Documents:
{json.dumps(doc_index, indent=2)}

Return JSON matching:
{{
  "matters": [
    {{
      "matter_name": "Standardized Concise Case Name",
      "doc_ids": [1, 2, 3],
      "confidence": 0.95,
      "reason": "Brief explanation of why these belong together"
    }}
  ],
  "unrelated_doc_ids": [4, 5]
}}
"""
    body = json.dumps({
        "model": SETTINGS["model"],
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": 1024},
        "format": "json"
    }).encode()

    req = urllib.request.Request(SETTINGS["ollama_url"].rstrip("/") + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
        raw_answer = result.get("response", "{}")
        parsed = json.loads(raw_answer) if isinstance(raw_answer, str) else raw_answer
        if not isinstance(parsed, dict):
            raise ValueError("Expected JSON object from model")
    except Exception as e:
        raise RuntimeError(f"Could not synthesize matter suggestions: {e}")

    raw_matters = parsed.get("matters", [])
    if not isinstance(raw_matters, list):
        raw_matters = []

    id_to_candidate = {c["id"]: c for c in candidates}
    all_assigned_ids = set()
    suggestions = []

    for item in raw_matters:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("matter_name", "")).strip()
        raw_name = re.sub(r"[/\\]+", " ", raw_name)
        raw_name = re.sub(r"[^\w &(),.\'-]", "", raw_name).strip(" .")[:100]
        if not raw_name or raw_name.lower() in ("null", "none", "uncategorized", "uncategorized office expenses"):
            continue

        doc_ids = item.get("doc_ids", [])
        if not isinstance(doc_ids, list):
            doc_ids = []
        valid_files = []
        assigned_in_this_matter = []
        for did in doc_ids:
            try:
                did_int = int(did)
            except (ValueError, TypeError):
                did_int = did
            if did_int in id_to_candidate:
                valid_files.append(id_to_candidate[did_int]["filename"])
                assigned_in_this_matter.append(did_int)
        if not valid_files:
            continue

        ckey = canonical_matter_key(raw_name)
        already_exists = False
        final_name = raw_name
        if ckey in existing_keys:
            already_exists = True
            final_name = existing_keys[ckey]

        try:
            conf = float(item.get("confidence", 0.9))
        except (ValueError, TypeError):
            conf = 0.9
        conf = max(0.0, min(1.0, conf))
        reason = str(item.get("reason", ""))[:300]

        suggestions.append({
            "name": final_name,
            "matter": final_name,
            "files": valid_files,
            "confidence": conf,
            "reason": reason,
            "already_exists": already_exists
        })
        all_assigned_ids.update(assigned_in_this_matter)

    unrelated_ids = parsed.get("unrelated_doc_ids", [])
    if not isinstance(unrelated_ids, list):
        unrelated_ids = []

    unrelated_files = []
    for did in unrelated_ids:
        try:
            did_int = int(did)
        except (ValueError, TypeError):
            did_int = did
        if did_int in id_to_candidate:
            unrelated_files.append(id_to_candidate[did_int]["filename"])

    for c in candidates:
        if c["id"] not in all_assigned_ids and c["filename"] not in unrelated_files:
            unrelated_files.append(c["filename"])

    return {
        "suggestions": suggestions,
        "unrelated_files": unrelated_files,
        "skipped_cooldown": skipped_cooldown,
        "unreadable": unreadable
    }


def pick_folder(initial=""):
    """Use the OS directory chooser via isolated subprocess so the server never crashes."""
    initial_path = str(Path(initial).expanduser().resolve()) if initial and Path(initial).exists() else str(Path.home())

    # 1. macOS osascript (native Cocoa Finder folder picker)
    if shutil.which("osascript"):
        clean_initial = str(initial_path).replace("\\", "\\\\").replace('"', '\\"')
        script = f'POSIX path of (choose folder with prompt "Choose a local folder" default location POSIX file "{clean_initial}")'
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            elif res.returncode == 1:
                return ""  # User cancelled
        except Exception:
            pass

    # 2. Linux zenity (native GTK dialog)
    if shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", "--title=Choose a local folder", f"--filename={initial_path}/"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            elif res.returncode == 1:
                return ""  # User cancelled
        except Exception:
            pass

    # 3. Linux kdialog (native Qt dialog)
    if shutil.which("kdialog"):
        cmd = ["kdialog", "--getexistingdirectory", initial_path, "--title", "Choose a local folder"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            elif res.returncode == 1:
                return ""  # User cancelled
        except Exception:
            pass

    # 4. Isolated Python child process with Tkinter (isolated so a crash cannot kill the server)
    code = f"""
import sys
try:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askdirectory(initialdir={repr(initial_path)}, title="Choose a local folder", mustexist=False)
    root.destroy()
    if path:
        print(path)
except Exception:
    sys.exit(1)
"""
    try:
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass

    return ""


def create_matter_folder(proposed):
    name = re.sub(r"[/\\]+", " ", str(proposed))
    name = re.sub(r"[^\w &(),.\'-]", "", name).strip(" .")[:100]
    if not name or name in (".", ".."):
        raise ValueError("Enter a valid matter folder name")
    proposed_key = canonical_matter_key(name)
    existing = matter_folders()
    for m in existing:
        if canonical_matter_key(m) == proposed_key:
            raise ValueError(f"A matter folder with this name already exists ('{m}')")
    root = safe_path(SETTINGS["library"])
    root.mkdir(parents=True, exist_ok=True)
    target = (root / name).resolve()
    target.relative_to(root)
    if target.exists():
        raise ValueError("That matter folder already exists")
    try:
        target.mkdir()
    except FileExistsError:
        raise ValueError("That matter folder already exists")
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
    _PROCESSED_REVIEW_CACHE.clear()
    inbox = safe_path(SETTINGS["inbox"])
    library = safe_path(SETTINGS["library"])
    inbox.mkdir(parents=True, exist_ok=True)
    library.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute("DELETE FROM activity")
    try:
        if str(APP) not in sys.path:
            sys.path.insert(0, str(APP))
        from scripts.generate_demo_files import generate_all_files, seed_matters
        seed_matters(library)
        generate_all_files(inbox)
        return
    except Exception:
        pass
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
    with db():
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _validate_request(self) -> bool:
        host = self.headers.get("Host", "").split(":")[0].strip().lower()
        if host not in ("127.0.0.1", "localhost", ""):
            self.send_json({"error": "Forbidden: invalid host"}, 403)
            return False
        return True

    def send_json(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        if not self._validate_request():
            return
        route = urlparse(self.path).path
        if route == "/api/state":
            self.send_json(dashboard_data())
        elif route == "/":
            raw = (APP / "static" / "index.html").read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        elif route in ("/favicon.svg", "/static/favicon.svg"):
            fav = APP / "static" / "favicon.svg"
            if fav.exists():
                raw = fav.read_bytes()
                self.send_response(200); self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(raw))); self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers(); self.wfile.write(raw)
            else:
                self.send_json({"error": "Not found"}, 404)
        elif route in ("/favicon.ico", "/static/favicon.ico"):
            fav = APP / "static" / "favicon.ico"
            if fav.exists():
                raw = fav.read_bytes()
                self.send_response(200); self.send_header("Content-Type", "image/x-icon")
                self.send_header("Content-Length", str(len(raw))); self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers(); self.wfile.write(raw)
            else:
                self.send_json({"error": "Not found"}, 404)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        if not self._validate_request():
            return
        content_type = self.headers.get("Content-Type", "")
        content_len = int(self.headers.get("Content-Length", "0") or 0)
        if content_len > 0 and "application/json" not in content_type:
            self.send_json({"error": "Content-Type must be application/json"}, 415)
            return
        global SETTINGS
        route = urlparse(self.path).path
        try:
            data = json.loads(self.rfile.read(content_len) or b"{}")
            if route == "/api/settings":
                with LOCK:
                    proposed = {**SETTINGS, **data}
                    proposed["inbox"] = str(safe_path(proposed["inbox"]))
                    proposed["library"] = str(safe_path(proposed["library"]))
                    proposed["unrelated_folder"] = str(safe_path(proposed.get("unrelated_folder", str(Path.home() / "Documents"))))
                    proposed["cooldown_seconds"] = max(0, min(86400, int(proposed["cooldown_seconds"])))
                    proposed["watch_interval_seconds"] = max(5, min(3600, int(proposed["watch_interval_seconds"])))
                    proposed["confidence_threshold"] = max(0.0, min(1.0, float(proposed["confidence_threshold"])))
                    for key in ("extensions", "excluded_extensions"):
                        if not isinstance(proposed[key], list): raise ValueError(f"{key} must be a list")
                        proposed[key] = sorted(set(str(x).lower() if str(x).startswith(".") else "." + str(x).lower() for x in proposed[key]))
                    if not is_local_url(proposed["ollama_url"]):
                        raise ValueError("For privacy, Ollama URL must point to this computer (127.0.0.1 or localhost)")
                    proposed["model"] = str(proposed["model"])[:100]
                    Path(proposed["inbox"]).mkdir(parents=True, exist_ok=True)
                    Path(proposed["library"]).mkdir(parents=True, exist_ok=True)
                    Path(proposed["unrelated_folder"]).mkdir(parents=True, exist_ok=True)
                    SETTINGS = proposed; save_settings()
                self.send_json({"ok": True, "state": dashboard_data()})
            elif route == "/api/pick-folder":
                kind = data.get("kind", "")
                initial_val = SETTINGS.get(kind, str(Path.home()))
                self.send_json({"path": pick_folder(initial_val)})
            elif route == "/api/discover":
                self.send_json(discover_matters())
            elif route == "/api/create-matter":
                with LOCK:
                    name = create_matter_folder(data.get("name", ""))
                    self.send_json({"ok": True, "name": name, "state": dashboard_data()})
            elif route == "/api/scan":
                self.send_json({"results": scan()})
            elif route == "/api/demo":
                init_demo(); self.send_json({"ok": True, "state": dashboard_data()})
            elif route == "/api/approve":
                with LOCK:
                    inbox = safe_path(SETTINGS["inbox"]); root = safe_path(SETTINGS["library"])
                    source = safe_path(data.get("source", "")); source.relative_to(inbox)
                    matter_name = str(data.get("matter", ""))
                    if matter_name not in matter_folders(): raise ValueError("Choose an existing matter")
                    dtype = re.sub(r"[^\w -]", "", str(data.get("document_type", "Other"))).strip()[:48] or "Other"
                    matter = (root / matter_name).resolve(); matter.relative_to(root)
                    destination_dir = (matter / dtype).resolve(); destination_dir.relative_to(matter)
                    if not source.is_file(): raise ValueError("The file is no longer in the inbox")
                    destination_dir.mkdir(exist_ok=True)
                    dest = unique_destination(destination_dir, source.name)
                    shutil.move(str(source), str(dest))
                    log_activity(source.name, source, dest, "filed_by_user", dtype, matter_name, 1.0, "Filed from review queue")
                    self.send_json({"ok": True, "state": dashboard_data()})
            elif route == "/api/exclude":
                with LOCK:
                    inbox = safe_path(SETTINGS["inbox"])
                    source = safe_path(data.get("source", ""))
                    source.relative_to(inbox)
                    if not source.is_file():
                        raise ValueError("The file is no longer in the inbox")
                    unrelated_dir = safe_path(SETTINGS.get("unrelated_folder", str(Path.home() / "Documents")))
                    unrelated_dir.mkdir(parents=True, exist_ok=True)
                    dest = unique_destination(unrelated_dir, source.name)
                    shutil.move(str(source), str(dest))
                    log_activity(source.name, source, dest, "excluded", "Unrelated", "", 1.0, "Moved out of inbox to Documents")
                    self.send_json({"ok": True, "state": dashboard_data()})
            else:
                self.send_json({"error": "Not found"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 400)


def watcher():
    while True:
        time.sleep(max(5, int(SETTINGS["watch_interval_seconds"])))
        if SETTINGS["watch_enabled"]:
            try:
                scan()
            except Exception:
                pass


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
    print("=" * 60)
    print("Briefly — Local-first legal filing assistant")
    print(f"Web UI:      http://127.0.0.1:8765")
    print(f"Inbox:       {safe_path(SETTINGS['inbox'])}")
    print(f"Library:     {safe_path(SETTINGS['library'])}")
    print(f"Ollama:      {SETTINGS['ollama_url']} (model: {SETTINGS['model']})")
    print("=" * 60)
    print("Tip: Use the 'Configure folders…' button in the web UI to point")
    print("     Briefly at your real inbox or document library anytime.")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBriefly stopped")
