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



# ----------------------------------------------------------------------------------------------
# Structure-aware native extraction (v2)
#
# WHY: the detector engine reads prose.  A raw PDF text dump contains things that are NOT the author's
# prose but look like "human typing errors" to the engine: running headers / Turnitin chrome
# ("Page 3 of 26 - ... Submission ID trn:oid:::..."), LaTeX equations flattened to glyph soup
# ("Pt = NLED ηo/e Vfw IDC, (1)"), table cells and figure labels (short "paragraphs" without a final
# full stop), paragraphs split in two by a display equation or a page break.  On a LaTeX manuscript
# this produced ~29 fake errors per 1000 words and turned a 75 % AI paper into a "9 % human" one.
# ----------------------------------------------------------------------------------------------
_MATH_FONT = re.compile(
    r"(?i)(?:^|\+)(?:cmmi|cmsy|cmex|cmmib|cmbsy|msam|msbm|lmmath|lmmi|lmsy|lmex|stix.*math|cambria.?math|"
    r"asana.?math|xits.?math|texgyre.*math|mathematicalpi|mt-?extra|symbol|euclid|sfmi|sfsy|txmi|txsy|"
    r"txex|pxmi|pxsy|rsfs|eufm|eusm|eurm)")
_BOLD_FONT = re.compile(r"(?i)(?:bold|cmbx|semibold|black|heavy|-bd|_bd|\bbd\b)")
_ITALIC_FONT = re.compile(r"(?i)(?:italic|oblique|cmti|cmsl|-it\b)")
_MATH_UNI = re.compile(r"[\u0370-\u03FF\u2200-\u22FF\u2A00-\u2AFF\u27C0-\u27EF\u2190-\u21FF\u2070-\u209F\u00B1\u00D7\u00F7\u221A]")
_BULLETS = set("•▪‣◦●■□–·∙")
_MARK = "\ue000"      # placeholder for a removed formula (never appears in real text)
_NOT_PROSE = {"log", "exp", "min", "max", "sin", "cos", "tan", "erfc", "erf", "clip", "ln", "sup", "inf", "lim",
              "det", "diag", "arg", "mod", "sgn", "rect", "sinc", "var", "cov", "dim", "ker", "tanh"}
_TURNITIN_LINE = re.compile(
    r"(?i)^\s*(?:page\s+\d+\s+of\s+\d+\s*[-–—]\s*.*|submission\s*id\b.*|trn:oid:+\S+|turnitin(?:\s+.*)?)\s*$")
_TURNITIN_FONT = re.compile(r"(?i)(?:notosans|lexend)")
_SENT_END = re.compile(r"[.!?؟…][\"'”’»)\]]*$")
_ITEM_START = re.compile(r"^\s*(?:[•▪‣◦●■□·∙\-–]\s+|\d{1,2}[.)]\s+(?=[A-Z\u0600-\u06FF])|\(?[a-z]\)\s+|\[\d{1,3}\]\s*)")
_REF_START = re.compile(r"^\s*\[\d{1,3}\]\s*\S")
_CAPTION_START = re.compile(r"(?i)^\s*(?:figure|fig\.|table|الشكل|شكل|الجدول|جدول)\s*\d+")


@dataclass
class _Unit:
    text: str
    page: int
    heading: bool = False      # bold / numbered section title
    item: bool = False         # starts a list item or a reference
    caption: bool = False
    wide: bool = True
    frag: bool = False         # short unterminated fragment: kept only if a continuation follows


def _span_is_math(span: dict, base_size: float) -> bool:
    font = str(span.get("font", ""))
    if _MATH_FONT.search(font):
        return True
    txt = span.get("text", "")
    if not txt.strip():
        return False
    # sub / superscripts: clearly smaller than the line's body text
    if base_size and float(span.get("size", base_size)) < 0.80 * base_size:
        return True
    return False


def _line_parts(line: dict):
    """-> (kept_text, math_chars, total_chars, bold_chars, base_size).  Math spans become one space."""
    spans = [s for s in line.get("spans", []) if s.get("text", "") != ""]
    if not spans:
        return "", 0, 0, 0, 0.0
    sizes = {}
    for s in spans:
        if s["text"].strip():
            sizes[round(float(s.get("size", 0)), 1)] = sizes.get(round(float(s.get("size", 0)), 1), 0) + len(s["text"])
    base = max(sizes, key=sizes.get) if sizes else float(spans[0].get("size", 0))
    out, math_c, total_c, bold_c = [], 0, 0, 0
    for s in spans:
        t = s["text"]
        n = len(t.replace(" ", ""))
        total_c += n
        # a lone bullet glyph (often typeset in a math font) is structure, not maths
        if t.strip() in _BULLETS or (_MATH_FONT.search(str(s.get("font", ""))) and t.strip() in ("•", "\x0f", "\u2022")):
            out.append("• ")
            continue
        if _span_is_math(s, base):
            if t.strip() in (".", ",") and out and out[-1][-1:].isdigit():
                out.append(t.strip())                  # decimal point of "31.6" typeset in math italics
                continue
            math_c += n
            out.append(_MARK)
            continue
        uni = len(_MATH_UNI.findall(t))
        if uni:
            math_c += uni
        if _BOLD_FONT.search(str(s.get("font", ""))):
            bold_c += n
        out.append(t)
    return "".join(out), math_c, total_c, bold_c, base


def _tidy_line(s: str) -> str:
    s = s.replace("\u00a0", " ").replace("\u200b", "")
    # a bracket whose only content was a formula/number-with-unit ("( 10 cm )") carries no prose
    def _drop_paren(m):
        inner = m.group(1)
        return " " if (_MARK in inner and _prose_words(inner.replace(_MARK, " ")) == 0) else m.group(0)
    s = re.sub(r"\(([^()]{0,60})\)", _drop_paren, s)
    s = s.replace(_MARK, " ")
    s = re.sub(r"\(\s+", "(", s)                          # "( x" -> "(x"
    s = re.sub(r"\s+\)", ")", s)                          # "x )" -> "x)"
    s = re.sub(r"\(\s*\)", " ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)               # "of , then" -> "of, then"
    s = re.sub(r"\s*[=≈≠≤≥<>±×·]\s*(?=[,.;:)]|$)", " ", s)  # dangling operator once its operand was cut
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def _prose_words(s: str) -> int:
    return sum(1 for w in re.findall(r"[^\W\d_]{3,}", s, re.U)
               if w.lower() not in _NOT_PROSE and (w.islower() or w.istitle()) and not re.search(r"[a-z][A-Z]", w))


def _mid_col_order(blocks, width):
    """Reading order for 1- and 2-column pages: full-width blocks split the page into bands."""
    if not blocks:
        return blocks
    mid = width / 2.0
    narrow = [b for b in blocks if (b["bbox"][2] - b["bbox"][0]) < 0.62 * width]
    left = [b for b in narrow if b["bbox"][2] <= mid + 0.04 * width]
    right = [b for b in narrow if b["bbox"][0] >= mid - 0.04 * width]
    two_col = len(left) >= 3 and len(right) >= 3 and len(left) + len(right) >= 0.6 * len(blocks)
    if not two_col:
        return sorted(blocks, key=lambda b: (round(b["bbox"][1], 0), b["bbox"][0]))
    wide = sorted([b for b in blocks if b not in left and b not in right], key=lambda b: b["bbox"][1])
    ordered, cursor = [], -1.0
    for w in wide + [None]:
        limit = w["bbox"][1] if w is not None else 1e9
        band = [b for b in blocks if (b in left or b in right) and cursor <= b["bbox"][1] < limit]
        ordered += sorted([b for b in band if b in left], key=lambda b: b["bbox"][1])
        ordered += sorted([b for b in band if b in right], key=lambda b: b["bbox"][1])
        if w is not None:
            ordered.append(w)
            cursor = w["bbox"][1]
    return ordered


def _norm_margin(s: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", s.strip().lower()))


def _detect_chrome(doc):
    """(turnitin_like, skip_pages, running_header_keys)."""
    n = len(doc)
    joined = []
    for i in range(min(n, 3)):
        joined.append(doc[i].get_text("text")[:1500])
    sample = "\n".join(joined)
    turnitin = bool(re.search(r"(?i)turnitin|trn:oid", sample)) and bool(re.search(r"(?i)page\s+\d+\s+of\s+\d+\s*[-–—]", sample))
    skip = set()
    if turnitin:
        for i in range(n):
            head = doc[i].get_text("text")[:600]
            if re.search(r"(?i)page\s+\d+\s+of\s+\d+\s*[-–—]\s*(?:cover page|ai writing overview|similarity overview|"
                         r"integrity overview|ai writing report|match groups|top sources)", head):
                skip.add(i)
    counts = {}
    for i in range(n):
        if i in skip:
            continue
        page = doc[i]
        h = page.rect.height
        seen = set()
        for b in page.get_text("blocks"):
            if len(b) >= 7 and b[6] != 0:
                continue
            y0, y1 = b[1], b[3]
            if y1 < 0.09 * h or y0 > 0.91 * h:
                for ln in str(b[4]).split("\n"):
                    key = _norm_margin(ln)
                    if key and key not in seen:
                        seen.add(key)
                        counts[key] = counts.get(key, 0) + 1
    thresh = max(3, int(0.3 * (n - len(skip))))
    running = {k for k, v in counts.items() if v >= thresh}
    return turnitin, skip, running


def _page_units(page, pno, turnitin, running):
    """Cleaned, ordered text units of one page (no cross-block merging yet)."""
    d = page.get_text("dict")
    h, w = page.rect.height, page.rect.width
    blocks = [b for b in d.get("blocks", []) if b.get("type", 0) == 0 and b.get("lines")]
    lw = sorted((ln["bbox"][2] - ln["bbox"][0]) for b in blocks for ln in b["lines"]
                if sum(len(sp.get("text", "")) for sp in ln.get("spans", [])) >= 30)
    ref_w = lw[int(0.9 * (len(lw) - 1))] if lw else 0.8 * w
    units = []
    for b in _mid_col_order(blocks, w):
        lines = []
        for ln in b["lines"]:
            if turnitin:
                ln = dict(ln, spans=[s for s in ln.get("spans", []) if not _TURNITIN_FONT.search(str(s.get("font", "")))])
            txt, mc, tc, bc, base = _line_parts(ln)
            if not txt.strip():
                continue
            raw_line = "".join(s.get("text", "") for s in ln.get("spans", []))
            if turnitin and _TURNITIN_LINE.match(raw_line):
                continue
            y0, y1 = ln["bbox"][1], ln["bbox"][3]
            if (y1 < 0.09 * h or y0 > 0.91 * h) and (_norm_margin(raw_line) in running or re.fullmatch(r"\s*\d{1,4}\s*", raw_line)):
                continue
            lines.append((txt, mc, tc, bc, ln["bbox"]))
        if not lines:
            continue
        # one typeset line can reach us as several fragments on the same baseline (inline maths splits it)
        merged_lines = []
        for item in sorted(lines, key=lambda x: (round((x[4][1] + x[4][3]) / 2.0), x[4][0])):
            if merged_lines:
                p = merged_lines[-1]
                ov = min(p[4][3], item[4][3]) - max(p[4][1], item[4][1])
                if ov >= 0.5 * min(p[4][3] - p[4][1], item[4][3] - item[4][1]):
                    bb = (min(p[4][0], item[4][0]), min(p[4][1], item[4][1]), max(p[4][2], item[4][2]), max(p[4][3], item[4][3]))
                    merged_lines[-1] = (p[0] + " " + item[0], p[1] + item[1], p[2] + item[2], p[3] + item[3], bb)
                    continue
            merged_lines.append(item)
        lines = merged_lines
        table_like = False
        if len(lines) >= 3:
            widths = [x[4][2] - x[4][0] for x in lines]
            full = sum(1 for wd in widths[:-1] if wd >= 0.80 * ref_w)
            table_like = full < 0.35 * max(1, len(widths) - 1)
        tot = sum(x[2] for x in lines) or 1
        math_ratio = sum(x[1] for x in lines) / tot
        bold_ratio = sum(x[3] for x in lines) / tot
        cleaned = [_tidy_line(x[0]) for x in lines]
        cleaned = [c for c in cleaned if c]
        if not cleaned:
            continue
        # whole block is an equation / figure label / table cell -> no authorship signal
        text_all = " ".join(cleaned)
        pw = _prose_words(text_all)
        n_words = len(text_all.split())
        is_head = bold_ratio >= 0.6 and n_words <= 18
        frag = False
        if not is_head:
            if table_like and not _CAPTION_START.match(text_all) and not _SENT_END.search(text_all[-3:]):
                continue                              # table body / axis labels / legend
            if math_ratio >= 0.30 and pw < 6:
                continue
            if pw < 4 and (math_ratio >= 0.12 or re.search(r"[=≈≤≥∑∫√]|\(\d+\)\s*$", text_all)):
                continue
            if n_words <= 12 and not _SENT_END.search(text_all) and not _CAPTION_START.match(text_all) \
                    and not _ITEM_START.match(text_all) and not text_all.rstrip().endswith(":") and len(cleaned) <= 2:
                frag = True                           # table cell / axis label / stray fragment (decided in _merge_units)
        # split the block into paragraphs/items and re-join wrapped lines
        parts, cur = [], ""
        for c in cleaned:
            starts_item = bool(_ITEM_START.match(c)) and (not cur or _SENT_END.search(cur) or cur.rstrip().endswith((";", ":")) or _REF_START.match(c))
            if starts_item and cur:
                parts.append(cur)
                cur = c
            elif not cur:
                cur = c
            elif re.search(r"[A-Za-z0-9\u0600-\u06FF]-$", cur) and re.match(r"[A-Za-z0-9\u0600-\u06FF]", c):
                cur = cur + c                          # "inter-" + "ference" / "8995-" + "1"
            else:
                cur = cur + " " + c
        if cur:
            parts.append(cur)
        for p in parts:
            p = _tidy_line(p)
            if len(p) < 2:
                continue
            units.append(_Unit(p, pno, heading=is_head and len(parts) == 1,
                               item=bool(_ITEM_START.match(p)), caption=bool(_CAPTION_START.match(p)),
                               wide=(b["bbox"][2] - b["bbox"][0]) > 0.6 * w, frag=frag and len(parts) == 1))
    return units


def _merge_units(units):
    """Rejoin paragraphs split by display equations, figures, columns or page breaks."""
    # 1) fragments survive only when the next unit visibly continues them (starts lower-case)
    kept = []
    for i, u in enumerate(units):
        if u.frag:
            nxt = units[i + 1] if i + 1 < len(units) else None
            if not (nxt is not None and nxt.text[:1].islower() and not nxt.frag):
                continue
        # exact repeats (text drawn twice, e.g. bold overlay) are one paragraph
        if kept and kept[-1].text == u.text and len(u.text) < 80:
            continue
        kept.append(u)
    paras = []          # [text, page, heading, caption, item]
    for u in kept:
        if paras and not u.heading and not u.item and not u.caption:
            prev = paras[-1]
            unfinished = not _SENT_END.search(prev[0]) or u.text[:1].islower()
            if unfinished and not prev[2] and (not prev[3] or u.text[:1].islower()):
                prev[0] = (prev[0] + u.text) if re.search(r"[A-Za-z0-9]-$", prev[0]) and u.text[:1].isalnum() \
                    else (prev[0] + " " + u.text)
                continue
        paras.append([u.text, u.page, u.heading, u.caption, u.item])
    return [(re.sub(r"[ \t]{2,}", " ", p[0]).strip(), p[1]) for p in paras if p[0].strip()]


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
        units: list = []
        ocr_pages: list[int] = []
        turnitin, skip_pages, running = _detect_chrome(doc)
        for number, page in enumerate(doc):
            if number in skip_pages:                  # Turnitin cover / overview pages are not the author's text
                continue
            native_units = _page_units(page, number, turnitin, running)
            native_text = " ".join(u.text for u in native_units)
            visible_count = sum(c.isalpha() for c in native_text)
            # A scanned page can have a selectable page number/header but its
            # actual content lives in a raster image. Don't silently ignore it.
            has_raster = bool(page.get_images(full=False))
            need_ocr = visible_count < 35 and (has_raster or not native_units)
            if need_ocr:
                if len(ocr_pages) >= MAX_OCR_PAGES:
                    raise ValueError(f"الملف يحوي أكثر من {MAX_OCR_PAGES} صفحة تحتاج OCR؛ قسّمه إلى ملفات أصغر.")
                try:
                    ocr_text = _ocr_page(page)
                except ValueError as exc:
                    raise ValueError(f"الصفحة {number + 1}: {exc}") from exc
                extracted = _paragraphs(ocr_text, ocr=True)
                if extracted:
                    native_units = [_Unit(t, number) for t in extracted]
                    ocr_pages.append(number + 1)
            if not native_units:
                # A genuinely empty PDF page is harmless. A scanned page that
                # yielded no text is not silently accepted as successfully read.
                if has_raster and number not in skip_pages and visible_count == 0 and not need_ocr:
                    pass
                elif has_raster:
                    raise ValueError(f"الصفحة {number + 1}: تعذّر استخراج نص من الصورة باستخدام OCR.")
                continue
            units.extend(native_units)
        merged = _merge_units(units)
        size = 0
        paragraphs: list[str] = []
        para_pages: list[int] = []
        for paragraph, pno in merged:
            size += len(paragraph)
            if size > MAX_EXTRACTED_CHARS:
                raise ValueError("نص PDF المستخرج كبير جدًا؛ قسّم المستند إلى أجزاء أصغر.")
            paragraphs.append(paragraph)
            para_pages.append(pno)
        if not paragraphs or not any(c.isalpha() for c in " ".join(paragraphs)):
            raise ValueError("لم يُعثر على نص قابل للفحص في PDF. تحقق من وضوح الصفحات واللغات المثبتة لـ OCR.")
        return PDFExtraction("\n".join(paragraphs), tuple(para_pages), len(doc), tuple(ocr_pages))
    finally:
        doc.close()


def extract_pdf_text(data: bytes) -> str:
    return extract_pdf(data).text
