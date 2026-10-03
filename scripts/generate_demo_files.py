#!/usr/bin/env python3
"""Generate realistic, anonymized legal test files for Briefly.

Captures the authentic file naming and document variety found on a
real legal practitioner's machine (motor injury, probate estate, commercial dispute,
generic scans, WhatsApp downloads, and non-legal office receipts).

Files are written directly into Briefly's configured inbox (by default in workspace/inbox),
which is ignored by Git, ensuring no private paths or test files are committed.
"""

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = PROJECT_DIR / "workspace"
SETTINGS_FILE = WORKSPACE_DIR / "settings.json"

def get_configured_paths() -> tuple[Path, Path]:
    """Read inbox and library from workspace settings, falling back to workspace defaults."""
    inbox = WORKSPACE_DIR / "inbox"
    library = WORKSPACE_DIR / "matters"
    if SETTINGS_FILE.exists():
        try:
            settings = json.loads(SETTINGS_FILE.read_text())
            if settings.get("inbox"):
                inbox = Path(os.path.expanduser(settings["inbox"])).resolve()
            if settings.get("library"):
                library = Path(os.path.expanduser(settings["library"])).resolve()
        except Exception:
            pass
    return inbox, library

def make_docx(path: Path, title: str, paragraphs: list[str]):
    esc_title = html.escape(title)
    body_xml = ''.join(
        f'<w:p><w:pPr><w:spacing w:after="160"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr><w:t>{html.escape(p)}</w:t></w:r></w:p>'
        for p in paragraphs
    )
    doc_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:jc w:val="center"/><w:spacing w:after="240"/></w:pPr><w:r><w:rPr><w:b/><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="26"/></w:rPr><w:t>{esc_title}</w:t></w:r></w:p>
    {body_xml}
  </w:body>
</w:document>'''

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''

    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('[Content_Types].xml', content_types)
        zf.writestr('_rels/.rels', rels)
        zf.writestr('word/document.xml', doc_xml)

def create_scanned_image_pdf(dest_pdf_path: Path):
    """Creates a PDF with no extractable text (<40 chars), triggering Briefly's scanned PDF detection."""
    pdf_content = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >> endobj
4 0 obj << /Length 20 >> stream
q
0 0 0 rg
0 0 0 0 re f
Q
endstream
endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000216 00000 n 
trailer << /Size 5 /Root 1 0 R >>
startxref
287
%%EOF"""
    dest_pdf_path.write_bytes(pdf_content)

def create_jpeg(dest_jpg_path: Path):
    """Minimal valid JPEG image."""
    jpg_bytes = bytes.fromhex(
        "ffd8ffe000104a46494600010101004800480000ffdb004300080606070605080707070909"
        "080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c3031"
        "3434341f27393d38323c2e333430ffc0000b080001000101011100ffc4001f00000105010101"
        "01010100000000000000000102030405060708090a0bda0008010100003f007f00ffd9"
    )
    dest_jpg_path.write_bytes(jpg_bytes)

def generate_all_files(target_dir: Path):
    target_dir.mkdir(parents=True, exist_ok=True)
    pdf_docs = {}
    
    # ==========================================
    # MATTER 1: Sterling v Gopaul & Colfire
    # (Motor Vehicle Accident / Personal Injury)
    # ==========================================
    pdf_docs["Statement of Case - Sterling v Gopaul & Colfire.pdf"] = (
        "IN THE HIGH COURT OF JUSTICE",
        [
            "REPUBLIC OF TRINIDAD AND TOBAGO",
            "Claim No. CV2025-01842",
            "BETWEEN: MARCUS STERLING (Claimant) and KEVIN GOPAUL & COLFIRE INSURANCE COMPANY LTD (Defendants)",
            "STATEMENT OF CASE",
            "1. The Claimant is an inventory manager residing at Curepe and at all material times was the lawful driver of motor vehicle registration number TCR 1928.",
            "2. On 14th March 2025, along the westbound carriageway of the Churchill Roosevelt Highway, the First Defendant negligently managed motor vehicle PBX 4412 causing a violent rear-end collision.",
            "3. By reason of the collision, the Claimant suffered severe cervical whiplash, acute clavicular fracture, and traumatic shock.",
            "AND THE CLAIMANT CLAIMS general damages, special damages of $84,500.00 TTD, and statutory interest."
        ]
    )

    # DOCX: Proposal for Settlement
    make_docx(
        target_dir / "08.09.26 2ND Proposal for Settlement CV2025-01842.docx",
        "WITHOUT PREJUDICE PROPOSAL FOR SETTLEMENT",
        [
            "Claim No. CV2025-01842 - Marcus Sterling v Kevin Gopaul, Charline Gopaul & Colfire Insurance Company Ltd.",
            "Dear Claims Counsel,",
            "We write pursuant to the pre-trial settlement conference held before the Master of the High Court.",
            "Having reviewed your preliminary counter-offer of $90,000.00 TTD, our client Marcus Sterling instructs us to submit this Second Proposal for Settlement.",
            "In full and final settlement of all claims arising from the motor vehicle accident of 14th March 2025, the Claimant will accept the sum of $145,000.00 TTD inclusive of agreed medical expenses and costs.",
            "Kindly confirm your position within fourteen (14) days."
        ]
    )

    # PDF: Hospital Discharge Summary
    pdf_docs["11.06.26 DISCHARGE SUMMARY.pdf"] = (
        "NORTH CENTRAL REGIONAL HEALTH AUTHORITY",
        [
            "ERIC WILLIAMS MEDICAL SCIENCES COMPLEX - HOSPITAL DISCHARGE SUMMARY",
            "Patient Name: Marcus Sterling | Age: 36 | Medical Record No: EWMSC-2025-8841",
            "Admission Date: 14th March 2025 | Discharge Date: 18th March 2025",
            "Admitting Diagnosis: Vehicular collision trauma, acute cervical strain, right clavicular fracture.",
            "Treatment & Course: Fitted with supportive arm sling and rigid cervical collar. Analgesic regimen initiated.",
            "Discharge Recommendations: Referred to Outpatient Orthopedic Physiotherapy Clinic for 12 weekly sessions.",
            "Attending Consultant Orthopedic Surgeon: Dr. E. Rahaman, FRCS."
        ]
    )

    # PDF: Medical Sick Leave Certificate
    pdf_docs["16.07.26 SICK LEAVE CERT. 28 DYS.pdf"] = (
        "MEDICAL ATTENDANCE CERTIFICATE",
        [
            "St. Augustine Private Medical Clinic | Dr. Rajiv Ramnath, MBBS",
            "Date of Consultation: 16th July 2026",
            "This is to certify that I have re-examined Mr. Marcus Sterling following clinical evaluation for severe chronic neck spasm and reduced spinal mobility stemming from a motor collision.",
            "In my clinical opinion, he remains fully incapacitated for heavy lifting or duties as an inventory manager.",
            "I recommend medical leave from work for a further period of twenty-eight (28) days with effect from 17th July 2026."
        ]
    )

    # PDF: Police Accident Report Receipt
    pdf_docs["17.03.26 POLICE ACCIDENT REPORT RECEIPT.pdf"] = (
        "TRINIDAD AND TOBAGO POLICE SERVICE",
        [
            "ST. JOSEPH POLICE STATION - TRAFFIC & PATROL DIVISION",
            "OFFICIAL POLICE ACCIDENT EXTRACT RECEIPT",
            "Receipt No: TTPS-ER-2025-4491 | Date Issued: 17th March 2026",
            "Applicant: Juris Legal Chambers on behalf of Marcus Sterling",
            "Accident Details: Collision between motor vehicles TCR 1928 (Sterling) and PBX 4412 (Gopaul) on 14/03/2025 along CR Highway.",
            "Fee Paid: $150.00 TTD. Station Official Stamp: St. Joseph Traffic Office."
        ]
    )

    # DOCX: Client Retainer Agreement
    make_docx(
        target_dir / "03.08.26 RETAINER AGREEMENT - Marcus Sterling.docx",
        "LEGAL RETAINER AND CLIENT CARE AGREEMENT",
        [
            "Client: Marcus Sterling",
            "Instructing Attorney: Juris Legal Chambers, Port of Spain",
            "Matter: Personal Injury and Damages Claim arising from road collision (Claim No. CV2025-01842 vs Kevin Gopaul and Colfire Insurance Company Ltd).",
            "Scope of Engagement: Conduct of High Court litigation, settlement negotiations, and trial advocacy.",
            "Terms: Professional fees and disbursements agreed in accordance with the Legal Profession Act."
        ]
    )

    # PDF: Duplicate file to test collision protection
    pdf_docs["16.07.26 SICK LEAVE CERT. 28 DYS (1).pdf"] = pdf_docs["16.07.26 SICK LEAVE CERT. 28 DYS.pdf"]

    # ==========================================
    # MATTER 2: Estate of Helena Blackwood
    # (Probate / Property / Conveyancing)
    # ==========================================
    pdf_docs["23.10.25 Notice of Application for Stay FHP 0043 of 2025.pdf"] = (
        "IN THE HIGH COURT OF JUSTICE",
        [
            "FAMILY AND PROBATE DIVISION",
            "Estate No. FHP 0043 of 2025",
            "IN THE ESTATE OF HELENA BLACKWOOD (DECEASED)",
            "NOTICE OF APPLICATION FOR AN INTERIM STAY OF PROCEEDINGS",
            "TAKE NOTICE that the Applicant Keith Blackwood will apply to the Court for an order that all further proceedings in the distribution of the Estate of Helena Blackwood be stayed pending determination of the caveat filed herein.",
            "Dated this 23rd day of October 2025."
        ]
    )

    pdf_docs["Affidavit in Support - Estate of Helena Blackwood.pdf"] = (
        "IN THE HIGH COURT OF JUSTICE",
        [
            "Estate No. FHP 0043 of 2025 - Estate of Helena Blackwood (Deceased)",
            "AFFIDAVIT IN SUPPORT OF APPLICATION FOR INJUNCTION",
            "I, Keith Blackwood, of Saddle Road, Maraval, make oath and say as follows:",
            "1. I am the eldest surviving son of the deceased Helena Blackwood who died on 4th August 2024.",
            "2. The purported last will and testament dated 12th January 2021 was executed when the deceased was in infirm health and lacked testamentary capacity.",
            "3. I pray this Honorable Court for an order preserving the estate assets, including the prime residential property at Lot 14 Maraval."
        ]
    )

    # DOCX: Deed of Conveyance
    make_docx(
        target_dir / "AMENDED Deed of Conveyance - Lot 14 Maraval.docx",
        "REPUBLIC OF TRINIDAD AND TOBAGO",
        [
            "DEED OF CONVEYANCE",
            "THIS DEED is made the 18th day of November 2025 between KEITH BLACKWOOD as legal personal representative of the Estate of Helena Blackwood, late of Maraval, of the One Part, and the Purchaser of the Other Part.",
            "WHEREAS the late Helena Blackwood was seized in unencumbered fee simple possession of all that parcel of land situate at Lot 14 Saddle Road, Maraval...",
            "NOW THIS DEED WITNESSETH that in consideration of the purchase price the Vendor conveys the hereditaments unto the Purchaser."
        ]
    )

    # PDF: Ambiguous scan filename with matter text inside
    pdf_docs["Scan_20260731 (1).pdf"] = (
        "RAYMOND & PIERRE CHARTERED VALUATION SURVEYORS",
        [
            "VALUATION APPRAISAL REPORT - CONFIDENTIAL",
            "Client: Legal Representatives of the Estate of Helena Blackwood",
            "Property: Lot 14 Saddle Road, Maraval (Freehold Residential Land and Dwelling)",
            "Inspection Date: 28th July 2026",
            "Current Open Market Valuation: $2,850,000.00 TTD (Two Million Eight Hundred and Fifty Thousand Dollars).",
            "Valuer: Trevor Pierre, FRICS, Chartered Valuation Surveyor."
        ]
    )

    # PDF: Interim Invoice
    pdf_docs["07.07.26 INTERIM INVOICE - Surveyor Fees.pdf"] = (
        "PROFESSIONAL FEE INVOICE - RAYMOND & PIERRE",
        [
            "Invoice No: RP-VAL-2026-092 | Date: 07.07.26",
            "Billed To: Juris Chambers, Re: Estate of Helena Blackwood",
            "For professional services rendered in respect of the inspection, boundary survey, and market valuation of Lot 14 Saddle Road, Maraval.",
            "Professional Fees: $6,500.00 TTD | VAT (12.5%): $812.50 | TOTAL DUE: $7,312.50 TTD."
        ]
    )

    # ==========================================
    # MATTER 3: Apex Logistics v TriniHaulage Ltd
    # (Commercial Freight Contract Litigation)
    # ==========================================
    make_docx(
        target_dir / "CLAIM FORM - Apex Logistics v TriniHaulage Ltd.docx",
        "IN THE HIGH COURT OF JUSTICE",
        [
            "Claim No. CV2024-00912",
            "BETWEEN: APEX LOGISTICS CARIBBEAN LIMITED (Claimant) and TRINIHAULAGE LIMITED (Defendant)",
            "CLAIM FORM FOR BREACH OF COMMERCIAL CONTRACT",
            "The Claimant claims against the Defendant the sum of $384,500.00 TTD being outstanding freight haulage, container storage, and demurrage fees under Commercial Master Services Agreement No. AL-2023-09.",
            "Interest pursuant to section 25 of the Supreme Court of Judicature Act at 6% per annum.",
            "Costs to be assessed if not agreed."
        ]
    )

    pdf_docs["22.10.25 ORDER OF COURT - Witness Statements Directions.pdf"] = (
        "IN THE HIGH COURT OF JUSTICE",
        [
            "Claim No. CV2024-00912",
            "BETWEEN: APEX LOGISTICS CARIBBEAN LIMITED (Claimant) and TRINIHAULAGE LIMITED (Defendant)",
            "ORDER OF THE COURT DATED 22ND OCTOBER 2025",
            "BEFORE THE HONOURABLE MADAME JUSTICE RAMPERSAD:",
            "IT IS ORDERED THAT:",
            "1. Standard disclosure of documents shall be completed on or before 15th November 2025.",
            "2. The parties shall file and exchange witness statements of facts by 15th December 2025.",
            "3. The Pre-Trial Review is fixed for 12th February 2026 in Courtroom POS 08."
        ]
    )

    # PDF: Ambiguous generic download name with matter text inside
    pdf_docs["Document_260814_1102.pdf"] = (
        "SUPREME COURT OF JUDICATURE",
        [
            "CIVIL DIVISION - NOTICE OF HEARING",
            "Matter: Claim No. CV2024-00912 - Apex Logistics Caribbean Limited v TriniHaulage Limited",
            "Take notice that the Case Management Conference in the above-captioned commercial matter will be heard virtually via Microsoft Teams on 14th August 2026 at 10:30 AM before Madam Justice Rampersad.",
            "Registrar of the Supreme Court."
        ]
    )

    pdf_docs["27.05.26 Pre-Action Protocol Letter re Freight Arrears.pdf"] = (
        "LEGAL PRE-ACTION PROTOCOL LETTER",
        [
            "Date: 27th May 2026",
            "To: The Managing Director, TriniHaulage Limited, Point Lisas Industrial Estate",
            "Dear Sirs,",
            "RE: COMMERCIAL DEBT AND DEMURRAGE CHARGES - APEX LOGISTICS CARIBBEAN LIMITED",
            "We act on behalf of Apex Logistics Caribbean Limited. Despite repeated reminders, the balance of $384,500.00 TTD remains outstanding.",
            "Take notice that unless payment is received within twenty-eight (28) days, our client will issue High Court proceedings without further notice."
        ]
    )

    # ==========================================
    # EDGE CASES & REVIEW QUEUE (The Guardrails!)
    # ==========================================

    # TXT: Unrelated Office Receipt (Ambiguous, stays in Review)
    (target_dir / "download (3).txt").write_text(
        "INVOICE & RECEIPT - OFFICE EXPRESS LTD\n"
        "Order Reference: OE-88412\n"
        "Date: 14/09/2026\n"
        "Items:\n"
        "- 1x Ergonomic Executive Mesh Chair ($1,299.00)\n"
        "- 1x Electric Standing Desk 60-inch ($1,151.00)\n"
        "TOTAL PAID: $2,450.00 TTD via Visa ending 4128.\n"
        "Thank you for your purchase!\n"
    )

    # PDF: Paper Supplies Receipt (Non-legal, stays in Review)
    pdf_docs["Receipt_Office_Supplies.pdf"] = (
        "STATIONERY MART TRINIDAD LTD",
        [
            "CASH SALE RECEIPT / TAX INVOICE",
            "Receipt No: SM-2026-9041 | Date: 19th September 2026",
            "Purchaser: Walk-in Customer (Cash)",
            "Items: 10 Boxes Letter Size Copier Paper ($450.00), 5 Packs Manilla Legal Filing Folders ($220.00), Heavy Duty Stapler ($150.00).",
            "Total Amount: $820.00 TTD. Paid in Full."
        ]
    )

    # BATCH CONVERT all PDF candidate documents
    with tempfile.TemporaryDirectory() as td:
        tmp_staging = Path(td)
        staging_docxs = []
        name_map = {}
        for pdf_name, (title, paras) in pdf_docs.items():
            safe_stem = f"doc_{len(staging_docxs)}"
            staging_docx = tmp_staging / f"{safe_stem}.docx"
            make_docx(staging_docx, title, paras)
            staging_docxs.append(str(staging_docx))
            name_map[f"{safe_stem}.pdf"] = pdf_name

        # Run single batch LibreOffice conversion
        try:
            cmd = [
                'libreoffice', '-env:UserInstallation=file:///tmp/lo_profile',
                '--headless', '--convert-to', 'pdf'
            ] + staging_docxs + ['--outdir', str(tmp_staging)]
            subprocess.run(cmd, check=True, capture_output=True)

            for gen_pdf in tmp_staging.glob("*.pdf"):
                if gen_pdf.name in name_map:
                    dest = target_dir / name_map[gen_pdf.name]
                    shutil.copy2(str(gen_pdf), str(dest))
        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError, OSError):
            # Fallback if LibreOffice is unavailable: write as clean .txt files
            for pdf_name, (title, paras) in pdf_docs.items():
                fallback_txt = target_dir / (Path(pdf_name).stem + ".txt")
                content = f"{title}\n\n" + "\n\n".join(paras)
                fallback_txt.write_text(content, encoding="utf-8")

    # PDF: Scanned PDF with No OCR Text -> Triggers Scanned PDF Detector
    create_scanned_image_pdf(target_dir / "Scan_Handwritten_Notes.pdf")

    # JPG: Excluded File Extension -> Skipped by watcher/scan
    create_jpeg(target_dir / "accident_scene_photo_01.jpg")

    # ZIP: Archive Bundle -> Flags archive extraction
    with zipfile.ZipFile(target_dir / "Client_Disclosures_Bundle.zip", "w") as z:
        z.writestr("notes.txt", "Client disclosure bundle notes")

    files = sorted(list(target_dir.iterdir()))
    # Set past timestamp (120s ago) so Briefly's 60s cooldown allows immediate processing
    past_time = time.time() - 120
    for f in files:
        if f.is_file():
            try:
                os.utime(f, (past_time, past_time))
            except OSError:
                pass

    print(f"SUCCESS: Generated {len(files)} realistic demo files in:\n  {target_dir}")
    for f in files:
        print(f"  - {f.name} ({f.stat().st_size:,} bytes)")
    return files

def seed_matters(library_path: Path):
    library_path.mkdir(parents=True, exist_ok=True)
    matters = [
        "Sterling v Gopaul & Colfire",
        "Estate of Helena Blackwood",
        "Apex Logistics v TriniHaulage Ltd"
    ]
    for m in matters:
        (library_path / m).mkdir(parents=True, exist_ok=True)
    print(f"Created {len(matters)} matter folders in:\n  {library_path}")

if __name__ == "__main__":
    configured_inbox, configured_library = get_configured_paths()

    parser = argparse.ArgumentParser(description="Generate realistic legal demo files for Briefly.")
    parser.add_argument(
        "--inbox",
        default=str(configured_inbox),
        help=f"Target inbox directory (defaults to configured inbox: {configured_inbox})"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing files in target inbox before generating"
    )
    parser.add_argument(
        "--seed-matters",
        action="store_true",
        help=f"Pre-create matter folders in Briefly's library ({configured_library})"
    )
    args = parser.parse_args()

    target_inbox = Path(os.path.expanduser(args.inbox)).resolve()

    if args.clean and target_inbox.exists():
        for item in target_inbox.iterdir():
            if item.is_file():
                item.unlink()
        print(f"Cleaned existing files from inbox: {target_inbox}")

    generate_all_files(target_inbox)

    if args.seed_matters:
        seed_matters(configured_library)
