# -*- coding: utf-8 -*-
"""Import PDF documents into the UNCHANGED Nebras text-analysis engine.

Native PDF text is extracted locally with PyMuPDF. Image-only/scanned pages
use local Tesseract OCR (Arabic+English if trained data is installed).
No OCR or PDF code changes the detector's scores, weights, or thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
import shutil
from pathlib import Path
import os

MAX_PAGES = 250
MAX_OCR_PAGES = 100
MAX_EXTRACTED_CHARS = 1_500_000


@dataclass(frozen=True)
class PDFExtraction:
    text: str
    para_pages: tuple[int, ...]  # page number (0-based) of each analysis paragraph
    page_count: int
    ocr_pages: tuple[int, ...]  # human-readable 1-based page numbers


def _paragraphs(raw: str, *, ocr: bool) -> list[str]:
    """Merge wrapped *visual* lines, retain genuine paragraph boundaries."""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    if ocr:
        groups = re.split(r"\n\s*\n", raw)
    else:
        groups = [raw]
    result = []
    for group in groups:
        # Joining visual lines prevents each short line from becoming an
        # independent <25-word paragraph in the original detection engine.
        lines = [line.strip() for line in group.split("\n") if line.strip()]
        if lines:
            result.append(" ".join(lines))
    return result


def _ocr_cmd() -> str | None:
    cmd = shutil.which("tesseract")
    if cmd:
        return cmd
    if os.name == "nt":
        for folder in (os.getenv("ProgramFiles"), os.getenv("ProgramFiles(x86)"),
                       os.getenv("LOCALAPPDATA")):
            if folder:
                exe = Path(folder) / "Tesseract-OCR" / "tesseract.exe"
                if exe.is_file():
                    return str(exe)
    return None


@lru_cache(maxsize=2)
def _ocr_languages(command: str) -> tuple[str, ...]:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = command
    return tuple(pytesseract.get_languages(config=""))


def _ocr_page(page) -> str:
    command = _ocr_cmd()
    if not command:
        raise ValueError(
            "الصفحة ممسوحة ضوئيًا وتحتاج OCR. ثبّت Tesseract OCR مع حزمتي اللغتين العربية (ara) "
            "والإنجليزية (eng)، ثم أعد فتح البرنامج. ملفات PDF النصية تعمل دون Tesseract."
        )
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise ValueError("لفحص PDF المصوّر يلزم تثبيت pytesseract وPillow عبر requirements.txt.") from exc
    pytesseract.pytesseract.tesseract_cmd = command
    try:
        languages = set(_ocr_languages(command))
    except Exception as exc:
        raise ValueError(f"تعذّر تشغيل OCR (Tesseract): {exc}") from exc
    candidates = [x for x in ("ara", "eng") if x in languages]
    if not candidates:
        raise ValueError("لم تُثبّت حزم لغات OCR العربية/الإنجليزية (ara, eng) داخل Tesseract.")
    # Arabic OCR must not silently proceed with English-only data.
    # When only one language is installed its limitations are documented in README.
    import pymupdf
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), colorspace=pymupdf.csRGB, alpha=False)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    try:
        return pytesseract.image_to_string(im, lang="+".join(candidates), config="--psm 3", timeout=100)
    except RuntimeError as exc:
        raise ValueError(f"تعذّر التعرف الضوئي على إحدى الصفحات: {exc}") from exc


@lru_cache(maxsize=3)
def extract_pdf(data: bytes) -> PDFExtraction:
    import pymupdf
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError("لا يمكن فتح PDF؛ تأكد أن الملف سليم وليس معطوبًا.") from exc
    try:
        if doc.needs_pass:
            raise ValueError("ملف PDF محمي بكلمة مرور. افتح نسخة غير مشفرة ثم ارفعها.")
        if not 1 <= len(doc) <= MAX_PAGES:
            raise ValueError(f"يجب أن يحتوي PDF على 1 إلى {MAX_PAGES} صفحة.")
        paragraphs: list[str] = []
        para_pages: list[int] = []
        ocr_pages: list[int] = []
        size = 0
        for number, page in enumerate(doc):
            native = []
            for block in page.get_text("blocks", sort=True):
                if len(block) >= 7 and block[6] != 0:  # image metadata, not text
                    continue
                native.extend(_paragraphs(str(block[4]), ocr=False))
            native_text = " ".join(native)
            visible_count = sum(c.isalpha() for c in native_text)
            # A scanned page can have a selectable page number/header but its
            # actual content lives in a raster image. Don't silently ignore it.
            has_raster = bool(page.get_images(full=False))
            need_ocr = visible_count < 35 and (has_raster or not native)
            if need_ocr:
                if len(ocr_pages) >= MAX_OCR_PAGES:
                    raise ValueError(f"الملف يحوي أكثر من {MAX_OCR_PAGES} صفحة تحتاج OCR؛ قسّمه إلى ملفات أصغر.")
                try:
                    ocr_text = _ocr_page(page)
                except ValueError as exc:
                    raise ValueError(f"الصفحة {number + 1}: {exc}") from exc
                extracted = _paragraphs(ocr_text, ocr=True)
                if extracted:
                    native = extracted
                    ocr_pages.append(number + 1)
            if not native:
                # A genuinely empty PDF page is harmless. A scanned page that
                # yielded no text is not silently accepted as successfully read.
                if has_raster:
                    raise ValueError(f"الصفحة {number + 1}: تعذّر استخراج نص من الصورة باستخدام OCR.")
                continue
            for paragraph in native:
                size += len(paragraph)
                if size > MAX_EXTRACTED_CHARS:
                    raise ValueError("نص PDF المستخرج كبير جدًا؛ قسّم المستند إلى أجزاء أصغر.")
                paragraphs.append(paragraph)
                para_pages.append(number)
        if not paragraphs or not any(c.isalpha() for c in " ".join(paragraphs)):
            raise ValueError("لم يُعثر على نص قابل للفحص في PDF. تحقق من وضوح الصفحات واللغات المثبتة لـ OCR.")
        return PDFExtraction("\n".join(paragraphs), tuple(para_pages), len(doc), tuple(ocr_pages))
    finally:
        doc.close()


def extract_pdf_text(data: bytes) -> str:
    return extract_pdf(data).text
