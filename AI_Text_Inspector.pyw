#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Text Inspector  v2.1
كاشف النصوص المولّدة بالذكاء الاصطناعي — يجمع بين "البصمة البشرية" (الأخطاء والتنسيق) و"بصمة التوليد":
سلامة الإحالات والمراجع، قوالب الصياغة النمطية، كثافة التفاصيل المحددة، أدوار الفقرات، وبيانات ملف Word.

- لا يحتاج أي مكتبة خارجية (Python 3.8+ فقط، وTkinter المضمّنة).
- تشغيل: دبل كليك على هذا الملف (.pyw) أو  python AI_Text_Inspector.pyw
- سطر الأوامر:
    python AI_Text_Inspector.pyw --cli file1.docx file2.txt
    python AI_Text_Inspector.pyw --html file.docx report.html   (تقرير HTML مفصّل بالأسباب وخريطة الفقرات)
    python AI_Text_Inspector.pyw --calibrate human_folder ai_folder   (معايرة الأوزان على عيّناتك)
"""
from __future__ import annotations

import os
import sys
import re
import csv
import json
import math
import html
import zipfile
import traceback
import datetime
from dataclasses import dataclass, field
from collections import Counter
from typing import List, Optional, Dict
import xml.etree.ElementTree as ET

VERSION = "2.1"
HERE = os.path.dirname(os.path.abspath(__file__))
WEIGHTS_FILE = os.path.join(HERE, "weights.json")

# ════════════════════════════════════════════════════════════════════════════
#  ثوابت ولغة
# ════════════════════════════════════════════════════════════════════════════
AR = "\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF"
AR_RE = re.compile("[%s]" % AR)
_L = r"(?:[^\W_]|[\u064B-\u0652\u0670])"
WORD_RE = re.compile(_L + r"+(?:['’\-]" + _L + r"+)*", re.UNICODE)
NORM_AR = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})

# kind -> (التسمية, الفئة, وزن الخطأ)
KINDS = {
    "space_before_punct": ("مسافة قبل علامة الترقيم", "spacing", 1.0),
    "no_space_after_punct": ("لا مسافة بعد علامة الترقيم", "spacing", 1.0),
    "double_space": ("مسافتان متتاليتان", "spacing", 0.8),
    "bracket_space": ("مسافة خاطئة حول الأقواس", "spacing", 0.8),
    "trailing_space": ("مسافة زائدة في نهاية الفقرة", "spacing", 0.5),
    "leading_space": ("مسافات/Tab في بداية الفقرة (تنسيق يدوي)", "spacing", 0.8),
    "repeat_punct": ("تكرار علامة ترقيم", "punct", 1.0),
    "mixed_punct_script": ("علامة ترقيم لاتينية داخل نص عربي", "punct", 0.8),
    "unbalanced": ("قوس/اقتباس غير مغلق أو زائد", "punct", 1.0),
    "no_final_punct": ("فقرة بلا علامة ختام", "punct", 0.4),
    "lower_start": ("جملة إنجليزية تبدأ بحرف صغير", "punct", 1.0),
    "repeated_word": ("كلمة مكررة", "typo", 1.2),
    "typo_en": ("خطأ إملائي إنجليزي شائع", "typo", 1.5),
    "ar_orth": ("خطأ إملائي عربي شائع (همزة/ياء/تاء)", "orth", 0.7),
    "mixed_digits": ("خلط الأرقام العربية والإنجليزية", "convention", 1.0),
    "mixed_quotes": ("خلط أنماط علامات الاقتباس", "convention", 1.0),
    "fmt_font": ("اختلاف نوع الخط داخل المتن", "format", 1.0),
    "fmt_size": ("اختلاف حجم الخط داخل المتن", "format", 1.0),
    "fmt_align": ("اختلاف محاذاة الفقرة", "format", 1.0),
    "fmt_spacing": ("اختلاف تباعد الفقرات/الأسطر", "format", 1.0),
    "fmt_indent": ("اختلاف المسافة البادئة", "format", 1.0),
    "fmt_empty_para": ("فقرات فارغة للتباعد", "format", 1.0),
    "fmt_hard_break": ("فواصل أسطر يدوية داخل الفقرة", "format", 1.0),
    "fmt_fake_heading": ("عنوان منسَّق يدويًا (ليس نمط عنوان)", "format", 1.0),
    "ai_phrase": ("عبارة شائعة في نصوص الذكاء الاصطناعي", "ai", 0.0),
    "ai_vocab": ("مفردة مرتبطة بنصوص النماذج اللغوية", "ai", 0.0),
    "ai_dash": ("شرطة قصيرة بديلة عن الشرطة الطويلة", "ai", 0.0),
    "ai_contrast": ("قالب تقابلي نمطي (ليس X بل Y)", "rhetoric", 0.0),
    "ai_vague": ("إسناد غامض بلا مصدر", "rhetoric", 0.0),
    "ai_signpost": ("عبارة توجيهية/تلخيصية نمطية", "rhetoric", 0.0),
    "ai_tricolon": ("ثلاثية متوازنة (أ، ب، وج)", "rhetoric", 0.0),
    "ai_enum": ("ترتيب ترتيبي نمطي", "rhetoric", 0.0),
    "ev_uncited": ("مرجع بلا إحالة داخل المتن", "evidence", 0.0),
    "ev_nocite": ("قائمة مراجع بلا أي إحالة في المتن", "evidence", 0.0),
    "ev_caption": ("تسمية جدول/شكل بلا إحالة أو بلا صورة", "evidence", 0.0),
    "ai_markdown": ("بقايا Markdown", "ai", 0.0),
    "ai_generic": ("صياغة عامة نمطية (one of the most / offers a wide range…)", "ai", 0.0),
    "ai_emdash": ("شرطة طويلة —", "ai", 0.0),
    "meta": ("بيانات الملف", "meta", 0.0),
}
CAT_LABELS = {
    "spacing": "المسافات", "punct": "الترقيم", "typo": "أخطاء الكتابة",
    "orth": "الإملاء العربي", "format": "تنسيق الوثيقة",
    "convention": "اتساق الأنماط", "ai": "علامات الذكاء الاصطناعي", "meta": "بيانات الملف",
    "rhetoric": "قوالب الصياغة النمطية", "evidence": "الأدلة والمراجع",
}
ERR_CATS = {"spacing", "punct", "typo"}          # تدخل في كثافة الأخطاء
CAP_PER_KIND = 400

# ── عبارات شائعة في نصوص النماذج اللغوية ───────────────────────────────────
AI_EN = [
    "delve into", "delves into", "delving into", "delved into", "delve", "tapestry", "testament to",
    "it is important to note", "it's important to note", "it is worth noting", "it's worth noting",
    "in conclusion", "in summary", "to summarize", "moreover", "furthermore", "additionally",
    "in today's", "rapidly evolving", "ever-evolving", "ever evolving", "landscape", "pivotal",
    "plays a crucial role", "play a crucial role", "plays a vital role", "play a vital role",
    "crucial role", "multifaceted", "holistic", "seamless", "seamlessly", "leverage", "leveraging",
    "foster", "fostering", "underscore", "underscores", "underscoring", "navigate the complexities",
    "realm of", "a myriad of", "myriad", "shed light on", "sheds light on", "paves the way",
    "intricate", "meticulous", "meticulously", "robust", "cutting-edge", "game-changer",
    "unlock the", "embark on", "journey", "comprehensive", "not only", "overall,",
    "as an ai", "i hope this helps", "let me know if", "in the realm of", "a testament",
]
AI_AR = [
    "علاوة على ذلك", "من الجدير بالذكر", "تجدر الإشارة إلى", "تجدر الاشارة", "في الختام", "في الخلاصة",
    "ختامًا", "ختاما", "في ظل", "يلعب دورًا", "يلعب دورا", "دورًا محوريًا", "دورا محوريا", "دورًا حاسمًا",
    "دورا حاسما", "في عالم اليوم", "في عصرنا الحالي", "التطور المتسارع", "التطورات المتسارعة",
    "بالإضافة إلى ذلك", "لا يمكن إنكار", "لا شك أن", "نسيج", "رحلة", "مشهد", "شامل", "متكامل",
    "بشكل فعال", "بصفة عامة", "أخيرًا وليس آخرًا", "يمكن القول", "من هنا تبرز", "ومن هنا", "على صعيد",
    "في سياق", "تعد من أبرز", "يعد من أهم", "تُعد من", "عصر التحول", "آفاق جديدة", "حجر الزاوية",
]
OPENERS = {
    "moreover", "furthermore", "additionally", "however", "consequently", "therefore", "thus",
    "overall", "notably", "importantly", "similarly", "likewise", "meanwhile", "nevertheless",
    "علاوه", "بالاضافه", "كما", "ومع", "لذلك", "وبالتالي", "ختاما", "اخيرا", "ولذلك", "وعليه",
    "وهكذا", "بالتالي", "ومن", "نتيجه", "اضافه", "علاوة",
}
OPENER_PAIRS = {"in addition", "in conclusion", "as a", "on the", "in summary", "in particular"}

TYPOS_EN = {
    "teh": "the", "recieve": "receive", "recieved": "received", "seperate": "separate",
    "definately": "definitely", "occured": "occurred", "occurence": "occurrence", "adress": "address",
    "wich": "which", "thier": "their", "untill": "until", "beleive": "believe", "acheive": "achieve",
    "enviroment": "environment", "goverment": "government", "accomodate": "accommodate",
    "occassion": "occasion", "neccessary": "necessary", "necesary": "necessary", "wierd": "weird",
    "truely": "truly", "existance": "existence", "independant": "independent", "publically": "publicly",
    "tommorow": "tomorrow", "begining": "beginning", "comming": "coming", "writting": "writing",
    "refered": "referred", "sucess": "success", "sucessful": "successful", "usefull": "useful",
    "becuase": "because", "becasue": "because", "wether": "whether", "alot": "a lot",
    "dont": "don't", "cant": "can't", "didnt": "didn't", "doesnt": "doesn't", "isnt": "isn't",
    "wasnt": "wasn't", "arent": "aren't", "couldnt": "couldn't", "wouldnt": "wouldn't",
    "shouldnt": "shouldn't", "thats": "that's", "analysys": "analysis", "anaylsis": "analysis",
    "resutls": "results", "reuslts": "results", "signifcant": "significant", "paramter": "parameter",
    "paramters": "parameters", "foundtion": "foundation", "foudation": "foundation",
    "stuctural": "structural", "strcutural": "structural", "conclusoin": "conclusion",
    "methdology": "methodology", "methodolgy": "methodology", "experiement": "experiment",
    "lenght": "length", "heigth": "height", "wieght": "weight", "diffrent": "different",
    "differnt": "different", "importent": "important", "improtant": "important", "knowlege": "knowledge",
    "occuring": "occurring", "persue": "pursue", "prefered": "preferred", "reccomend": "recommend",
    "relevent": "relevant", "succesful": "successful", "supress": "suppress", "tendancy": "tendency",
    "thoughout": "throughout", "transfered": "transferred", "unfortunatly": "unfortunately",
    "wheather": "weather", "writen": "written", "yeild": "yield", "accross": "across",
    "agressive": "aggressive", "apparant": "apparent", "arguement": "argument", "calender": "calendar",
}
ORTH_AR = [
    ("الي", "إلى"), ("الى", "إلى"), ("علي", "على"), ("حتي", "حتى"), ("متي", "متى"),
    ("التى", "التي"), ("الذى", "الذي"), ("فى", "في"), ("هذة", "هذه"), ("هاذا", "هذا"), ("لاكن", "لكن"),
    ("انشاء", "إنشاء"), ("اهمية", "أهمية"), ("اهم", "أهم"), ("اكثر", "أكثر"), ("اقل", "أقل"),
    ("اخرى", "أخرى"), ("اخر", "آخر"), ("ادارة", "إدارة"), ("اسلوب", "أسلوب"), ("اساسي", "أساسي"),
    ("اهداف", "أهداف"), ("اجراء", "إجراء"), ("اجراءات", "إجراءات"), ("ايضا", "أيضًا"),
    ("اذا", "إذا"), ("لان", "لأن"), ("انه", "أنه"), ("انها", "أنها"), ("انهم", "أنهم"), ("اكد", "أكد"),
    ("نتيجه", "نتيجة"), ("دراسه", "دراسة"), ("مرحله", "مرحلة"), ("طريقه", "طريقة"),
    ("نظريه", "نظرية"), ("مشكله", "مشكلة"), ("عمليه", "عملية"), ("منطقه", "منطقة"),
    ("اللذي", "الذي"),
]
ABBREV = {"e.g", "i.e", "etc", "vs", "fig", "figs", "eq", "eqs", "al", "no", "approx", "dr", "mr", "mrs",
          "prof", "ref", "refs", "sec", "vol", "pp", "cf", "st", "inc", "ltd", "co", "ph", "d", "viz"}
REPEAT_OK = {"that", "had", "bye", "no", "so", "very", "ha", "la", "yeah", "ندى"}

# ════════════════════════════════════════════════════════════════════════════
#  الأوزان
# ════════════════════════════════════════════════════════════════════════════
# الموجب = يرفع احتمال الذكاء الاصطناعي ، السالب = يرفع احتمال الكتابة البشرية
FEATURES = [
    # ── أسلوب وصياغة ──
    "burst_low", "para_uniform", "sent_uniform", "ai_lexicon", "ai_vocab", "contrast", "vague_attr",
    "signpost", "tricolon", "enumer", "markdown", "emdash", "openers", "evidence_low",
    # ── (جديد 2.1) نثر محايد مصقول ──
    "generic_phr", "frame", "punct_flat", "no_voice",
    # ── أدلة ومراجع وبنية ──
    "cite_gap", "caption_orphan", "template",
    # ── ملف Word ──
    "meta_gen", "fmt_perfect",
    # ── نظافة / آثار بشرية ──
    "clean", "err_density", "fmt_incons", "meta_human", "mixed_conv", "ortho", "voice",
]
DEFAULT_WEIGHTS = {
    "bias": -3.6,
    "burst_low": 0.6, "para_uniform": 0.3, "sent_uniform": 0.6, "ai_lexicon": 1.3, "ai_vocab": 1.3,
    "contrast": 1.5, "vague_attr": 1.4, "signpost": 0.8, "tricolon": 0.9, "enumer": 0.5,
    "markdown": 1.4, "emdash": 0.4, "openers": 0.35, "evidence_low": 1.0,
    "generic_phr": 3.0, "frame": 1.0, "punct_flat": 0.4, "no_voice": 0.3,
    "cite_gap": 2.0, "caption_orphan": 1.2, "template": 0.7,
    "meta_gen": 1.8, "fmt_perfect": 0.5,
    "clean": 0.25,
    "err_density": -2.4, "fmt_incons": -1.8, "meta_human": -2.2, "mixed_conv": -1.2, "ortho": -1.0, "voice": -1.0,
}
FEATURE_LABELS = {
    "burst_low": "تجانس طول الجمل (قلة التباين)",
    "para_uniform": "تجانس طول الفقرات",
    "sent_uniform": "انحصار أغلب الجمل في نطاق طول ضيّق",
    "ai_lexicon": "كثافة العبارات الشائعة في نصوص الذكاء الاصطناعي",
    "ai_vocab": "كثافة المفردات الأكاديمية-المولَّدة الشائعة",
    "contrast": "قوالب التقابل النمطية (ليس X بل Y / X قبل أن يكون Y)",
    "vague_attr": "إسناد غامض لأدلة بلا مصدر (تشير الدراسات…)",
    "signpost": "عبارات توجيهية/تلخيصية نمطية (المحور المركزي، في هذا المعنى…)",
    "tricolon": "كثرة الثلاثيات المتوازنة (أ، ب، وج)",
    "enumer": "ترتيب ترتيبي نمطي (الأول… الثاني… الثالث…)",
    "markdown": "بقايا Markdown (** ، ## ، ---)",
    "emdash": "الشرطة الطويلة أو بدائلها ( - )",
    "openers": "كثرة أدوات الربط في بداية الجمل",
    "evidence_low": "ندرة التفاصيل المحددة (أرقام، أسماء، قياسات، مواقع)",
    "generic_phr": "كثافة الصياغة العامة المحايدة (one of the most، offers a wide range، Whether…)",
    "frame": "إطار مقال نمطي: افتتاحية تعريفية عامة + خاتمة تلخيصية",
    "punct_flat": "فقر علامات الترقيم (لا أقواس ولا اقتباسات ولا نقطتان ولا فاصلة منقوطة ولا أسئلة)",
    "no_voice": "غياب أي صوت شخصي في نص طويل (لا ضمير متكلم/مخاطب، لا اختصارات، لا أسئلة)",
    "cite_gap": "مراجع بلا إحالات داخل المتن",
    "caption_orphan": "جداول/أشكال بلا إحالة في النص أو بلا صورة فعلية",
    "template": "قالب بنيوي مثالي (ملخص، كلمات مفتاحية، ترقيم متسلسل، أهداف i-v…)",
    "meta_gen": "بصمة توليد برمجي في بيانات ملف Word",
    "fmt_perfect": "اتساق تنسيق تام بلا أي أثر لتحرير يدوي",
    "clean": "نظافة مفرطة (خلو من الأخطاء الطباعية)",
    "err_density": "كثافة أخطاء المسافات والترقيم والكتابة",
    "fmt_incons": "عدم اتساق تنسيق المتن (داخل الدور الواحد)",
    "meta_human": "آثار تحرير بشري في بيانات ملف Word",
    "mixed_conv": "خلط أنماط الأرقام/الاقتباس",
    "ortho": "أخطاء إملائية عربية شائعة",
    "voice": "صوت شخصي (ضمير المتكلم، اختصارات، أسئلة، تعجب)",
}
# عتبات المعايرة (مستخرجة من قياس نصوص بشرية ونصوص مولَّدة، وتُعدَّل من هنا)
TUNE = {
    "cv_hi": 0.70, "cv_lo": 0.38,           # تباين طول الجمل: ≥hi بشري تمامًا ، ≤lo متجانس جدًا
    "mid_lo": 0.40, "mid_hi": 0.80,         # حصة الجمل التي طولها 12–32 كلمة
    "para_cv_hi": 0.60, "para_cv_lo": 0.25,
    "spec_hi": 30.0,                         # كثافة التفاصيل المحددة لكل 1000 كلمة (فوقها = تفاصيل غنية)
    "vocab_scale": 14.0, "lex_scale": 6.0,
    "contrast_scale": 2.5, "vague_scale": 1.5, "signpost_scale": 2.0,
    "tricolon_lo": 0.10, "tricolon_hi": 0.40,
    "gen_lo": 6.0, "gen_hi": 22.0,           # كثافة الصياغة العامة لكل 1000 كلمة (البشري ≤ ~6، المولَّد ≥ ~19)
    "punct_lo": 2.0, "punct_hi": 15.0,       # علامات الترقيم «النادرة» لكل 1000 كلمة
}


def load_weights() -> Dict[str, float]:
    w = dict(DEFAULT_WEIGHTS)
    try:
        if os.path.exists(WEIGHTS_FILE):
            with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("_version") == VERSION:       # أوزان معايرة لنفس النسخة فقط (تغيّرت المؤشرات في 2.1)
                w.update({k: float(v) for k, v in data.items() if k in w})
    except Exception:
        pass
    return w


# ════════════════════════════════════════════════════════════════════════════
#  هياكل البيانات
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class Finding:
    kind: str
    para: int
    start: int
    end: int
    text: str = ""
    note: str = ""

    @property
    def cat(self) -> str:
        return KINDS[self.kind][1]

    @property
    def label(self) -> str:
        return KINDS[self.kind][0]


@dataclass
class Para:
    text: str = ""
    runs: list = field(default_factory=list)   # (start, end, font, size, bold, italic)
    style_name: str = ""
    is_heading: bool = False
    in_table: bool = False
    is_list: bool = False
    align: str = "left"
    after: Optional[int] = None
    before: Optional[int] = None
    line: Optional[int] = None
    first: int = 0
    hard_breaks: int = 0
    has_drawing: bool = False
    role: str = "body"          # body / heading / caption / reference / keywords / table / list / front / empty

    @property
    def is_body(self) -> bool:
        return self.role == "body" and len(self.text.strip()) >= 40


@dataclass
class Report:
    source: str = ""
    texts: List[str] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    features: Dict[str, tuple] = field(default_factory=dict)   # name -> (value, weight, contribution)
    fvals: Dict[str, float] = field(default_factory=dict)
    score: float = 50.0
    z: float = 0.0
    confidence: float = 0.0
    verdict: str = ""
    color: str = "gray"
    words: int = 0
    sentences: int = 0
    stats: Dict[str, float] = field(default_factory=dict)
    meta: Dict[str, str] = field(default_factory=dict)
    is_docx: bool = False
    reasons: List[str] = field(default_factory=list)            # أسباب الحكم بالعربية
    para_scores: List[tuple] = field(default_factory=list)      # (فقرة, درجة 0-100, عدد الكلمات)
    ctx: Dict[str, float] = field(default_factory=dict)

    def count_by_kind(self) -> Counter:
        return Counter(f.kind for f in self.findings)

    def count_by_cat(self) -> Counter:
        return Counter(f.cat for f in self.findings)


# ════════════════════════════════════════════════════════════════════════════
#  فحوص النص (مسافات / ترقيم / إملاء)
# ════════════════════════════════════════════════════════════════════════════
RX_SPACE_BEFORE = re.compile(r"(?<=\S)[ \t\u00A0]+(?=[,;:!?،؛؟]|\.(?!\.))")
RX_NSA_COMMA = re.compile(r"(?<=[^\W\d_])[,;،؛](?=[^\W\d_])")
RX_NSA_COLON = re.compile(r"(?<=[^\W\d_]{2}):(?=[^\W\d_])")
RX_NSA_PERIOD_LAT = re.compile(r"(?<=[a-z]{2})[.!?](?=[A-Z][a-z])")
RX_NSA_PERIOD_AR = re.compile("(?<=[%s]{2})[.!؟?](?=[%s]{2})" % (AR, AR))
RX_DBL = re.compile(r"(?<=\S)[ \u00A0]{2,}(?=\S)")
RX_BR_IN = re.compile(r"\([ \t]+(?=\S)|(?<=\S)[ \t]+\)")
RX_BR_ATTACH = re.compile(r"(?<=[^\W\d_]{3})\((?!s\))(?=[^\W_])")
RX_BR_AFTER = re.compile(r"\)(?=[^\W\d_])")
RX_REP = re.compile(r"([,;:!?،؛؟])\1+|(?<![.\u2026])\.\.(?![.])")
RX_REPWORD = re.compile(r"(?<!\w)([^\W\d_]{2,})[ \t]+\1(?!\w)", re.I | re.U)
RX_LATIN_IN_AR = re.compile("(?<=[%s])[ ]?[,;?][ ]?(?=[%s])" % (AR, AR))
RX_LOWER_START = re.compile(r"\b([A-Za-z]{3,})([.!?])[ \t]+([a-z])")
RX_I = re.compile(r"(?<![\w'’.])i(?=[ \t]|'|’)(?![.\w-])")
RX_TRAIL = re.compile(r"[ \t\u00A0]+$")
RX_LEAD = re.compile(r"^(?:[ \u00A0]{2,}|\t+)(?=\S)")
RX_LIST_MARK = re.compile(r"(?:^|\s)[\dA-Za-z\u0600-\u06FF]{1,2}\)")
RX_TYPO_EN = re.compile(r"\b(" + "|".join(sorted(map(re.escape, TYPOS_EN), key=len, reverse=True)) + r")\b", re.I)
RX_ORTH = [
    (re.compile("(?<![%s])(?:[وفبلك])?(?:ال)?%s(?![%s\u064B-\u0652])" % (AR, re.escape(w), AR)), w, r)
    for w, r in ORTH_AR
]


def _mk(kind, i, t, s, e, note="") -> Finding:
    a, b = max(0, s - 18), min(len(t), e + 18)
    snip = t[a:b].replace("\t", "⇥").replace("\n", " ")
    return Finding(kind, i, s, e, snip, note)


class _Sink:
    def __init__(self):
        self.items: List[Finding] = []
        self.n = Counter()

    def add(self, kind, i, t, s, e, note=""):
        if self.n[kind] >= CAP_PER_KIND:
            self.n[kind] += 1
            return
        self.n[kind] += 1
        self.items.append(_mk(kind, i, t, s, e, note))


def check_paragraph(i: int, t: str, p: Para, sink: _Sink):
    if not t.strip() or p.role == "reference":
        return
    ar_dom = len(AR_RE.findall(t)) > 0.3 * max(1, sum(c.isalpha() for c in t))

    for m in RX_SPACE_BEFORE.finditer(t):
        sink.add("space_before_punct", i, t, m.start(), m.end() + 1, "مسافة قبل «%s»" % t[m.end():m.end() + 1])
    for rx, note in ((RX_NSA_COMMA, "بعد الفاصلة/الفاصلة المنقوطة"), (RX_NSA_COLON, "بعد النقطتين"),
                     (RX_NSA_PERIOD_LAT, "بعد نهاية الجملة"), (RX_NSA_PERIOD_AR, "بعد نهاية الجملة")):
        for m in rx.finditer(t):
            sink.add("no_space_after_punct", i, t, m.start(), m.end() + 1, "لا مسافة " + note)
    for m in RX_DBL.finditer(t):
        sink.add("double_space", i, t, m.start(), m.end())
    for m in RX_BR_IN.finditer(t):
        sink.add("bracket_space", i, t, m.start(), m.end(), "مسافة داخل القوس")
    for m in RX_BR_ATTACH.finditer(t):
        sink.add("bracket_space", i, t, m.start(), m.end(), "قوس ملتصق بالكلمة قبله")
    for m in RX_BR_AFTER.finditer(t):
        sink.add("bracket_space", i, t, m.start(), m.end() + 1, "كلمة ملتصقة بالقوس المغلق")
    for m in RX_REP.finditer(t):
        sink.add("repeat_punct", i, t, m.start(), m.end())
    for m in RX_REPWORD.finditer(t):
        if m.group(1).lower() not in REPEAT_OK:
            sink.add("repeated_word", i, t, m.start(), m.end(), "«%s» مكررة" % m.group(1))
    if ar_dom:
        for m in RX_LATIN_IN_AR.finditer(t):
            sink.add("mixed_punct_script", i, t, m.start(), m.end(), "علامة لاتينية بين كلمتين عربيتين")
    for m in RX_LOWER_START.finditer(t):
        if m.group(1).lower() not in ABBREV:
            sink.add("lower_start", i, t, m.start(3), m.end(3), "بعد «%s»" % (m.group(1) + m.group(2)))
    for m in RX_I.finditer(t):
        sink.add("typo_en", i, t, m.start(), m.end(), "الضمير I بحرف صغير")
    for m in RX_TYPO_EN.finditer(t):
        sink.add("typo_en", i, t, m.start(), m.end(), "الصواب: " + TYPOS_EN[m.group(1).lower()])
    for rx, wrong, right in RX_ORTH:
        for m in rx.finditer(t):
            sink.add("ar_orth", i, t, m.start(), m.end(), "الصواب غالبًا: " + right)
    m = RX_TRAIL.search(t)
    if m:
        sink.add("trailing_space", i, t, m.start(), m.end())
    m = RX_LEAD.match(t)
    if m and not p.is_list:
        sink.add("leading_space", i, t, 0, m.end(), "إزاحة بالمسافات/Tab بدل المسافة البادئة")

    # توازن الأقواس والاقتباس
    list_mark = bool(RX_LIST_MARK.search(t))
    for o, c in (("(", ")"), ("[", "]"), ("{", "}"), ("“", "”"), ("«", "»")):
        if o in "([" and list_mark:
            continue
        if t.count(o) != t.count(c):
            ch = o if t.count(o) > t.count(c) else c
            idx = t.find(ch)
            sink.add("unbalanced", i, t, idx, idx + 1, "عدد «%s» لا يساوي «%s»" % (o, c))
    if t.count('"') % 2 == 1:
        idx = t.find('"')
        sink.add("unbalanced", i, t, idx, idx + 1, "علامة اقتباس مزدوجة مفردة")

    # فقرة جوهرية بلا علامة ختام
    words = len(WORD_RE.findall(t))
    if words >= 8 and p.role == "body":
        last = t.rstrip()[-1:]
        if last and (last.isalnum() or last in ")]}") and not re.match(r"^\s*(?:[-*•]|\d+[.)])\s", t):
            sink.add("no_final_punct", i, t, len(t.rstrip()) - 1, len(t.rstrip()), "الفقرة لا تنتهي بعلامة ترقيم")


def check_conventions(texts: List[str], sink: _Sink):
    full = "\n".join(texts)
    wd = len(re.findall(r"[0-9]", full))
    ad = len(re.findall(r"[\u0660-\u0669\u06F0-\u06F9]", full))
    if wd and ad and min(wd, ad) >= 3 and min(wd, ad) / (wd + ad) >= 0.15:
        rx = re.compile(r"[0-9]+") if wd < ad else re.compile(r"[\u0660-\u0669\u06F0-\u06F9]+")
        for i, t in enumerate(texts):
            for m in rx.finditer(t):
                sink.add("mixed_digits", i, t, m.start(), m.end(),
                         "أرقام غربية بين أرقام عربية" if wd < ad else "أرقام عربية بين أرقام غربية")
    styles = {
        "dq_straight": re.compile(r'"'), "dq_curly": re.compile(r"[“”]"), "dq_guil": re.compile(r"[«»]"),
    }
    cnt = {k: len(rx.findall(full)) for k, rx in styles.items()}
    used = [k for k, v in cnt.items() if v >= 2]
    if len(used) >= 2:
        major = max(used, key=lambda k: cnt[k])
        for k in used:
            if k == major:
                continue
            for i, t in enumerate(texts):
                for m in styles[k].finditer(t):
                    sink.add("mixed_quotes", i, t, m.start(), m.end(), "نمط اقتباس مختلف عن السائد")
    s2 = len(re.findall(r"(?<=\w)'(?=\w)", full))
    c2 = len(re.findall(r"(?<=\w)’(?=\w)", full))
    if s2 >= 2 and c2 >= 2:
        rx = re.compile(r"(?<=\w)'(?=\w)") if s2 < c2 else re.compile(r"(?<=\w)’(?=\w)")
        for i, t in enumerate(texts):
            for m in rx.finditer(t):
                sink.add("mixed_quotes", i, t, m.start(), m.end(), "فاصلة عليا بنمط مختلف عن السائد")


# ════════════════════════════════════════════════════════════════════════════
#  مؤشرات الذكاء الاصطناعي
# ════════════════════════════════════════════════════════════════════════════
def _build_lexicon_rx():
    parts = []
    for ph in AI_EN:
        parts.append(re.escape(ph).replace(r"\ ", r"\s+"))
    dia = "[\u064B-\u0652]*"
    for ph in AI_AR:
        n = ph.translate(NORM_AR)
        seg = []
        for ch in n:
            seg.append(r"\s+" if ch == " " else re.escape(ch) + (dia if AR_RE.match(ch) else ""))
        parts.append("".join(seg))
    parts.sort(key=len, reverse=True)
    return re.compile(r"(?<![\w])(?:" + "|".join(parts) + r")(?![\w])", re.I | re.U)


RX_LEXICON = _build_lexicon_rx()


# ── قوالب «الصياغة العامة المحايدة» ─────────────────────────────────────────
# النص المولَّد لا يحمل دائمًا عبارات أكاديمية ظاهرة (delve, robust…)؛ كثيرًا ما يكون نثرًا محايدًا مصقولًا
# تتكرر فيه مفاتيح مثل «one of the most»، «offers a wide range of»، «Whether a traveler is interested in…»،
# «Overall,». كل عنصر (نمط، وزن). الوزن الكبير = يندر في الكتابة البشرية بهذه الكثافة.
GEN_EN = [
    (r"\bone of the (?:most|world's|leading|largest|oldest|greatest|major)\b", 1.0),
    (r"\bplays? (?:an?|a very) (?:\w+ )?(?:important|significant|vital|key|central|major|crucial|essential|critical|meaningful|valuable|fundamental)(?: \w+)? role\b", 1.2),
    (r"\ba (?:wide|broad|diverse|rich|vast|great|growing) (?:range|variety|array|selection|mixture|combination|blend|diversity|number)(?: of)?\b", 1.0),
    (r"\brich (?:and \w+ |cultural |historical )?(?:heritage|history|culture|tradition|mixture|combination|tapestry|legacy)\b", 1.0),
    (r"\b(?:remarkable|extraordinary|unforgettable|fascinating|captivat\w+|vibrant|stunning|breathtaking|enchant\w+|iconic|memorable|impressive|exceptional|distinctive|thriving|rewarding|enjoyable|welcoming|undeniable|meaningful|unique|diverse)\b", 0.6),
    (r"\b(?:offers?|provides?|offering|providing) (?:a|an|visitors|travelers|tourists|people|many|numerous|several|something|valuable|unique|rich|wide|endless)\b", 0.8),
    (r"\bsomething for (?:every|everyone)\b", 1.5),
    (r"\bwhether (?:a|an|someone|you|one|the)\b[^.]{0,70}?\b(?:is|are|interested|looking|prefer|seeking)\b", 1.5),
    (r"\bfor this reason\b", 1.0), (r"\b(?:increasingly|ever-increasing|ever-growing)\b", 0.8),
    (r"\bcontribut\w+ (?:to|greatly|significantly)\b", 0.8),
    (r"\bin recent years\b", 0.8), (r"\bon the (?:other|positive|negative) (?:hand|side)\b", 0.8),
    (r"\bmaking it (?:a|an|one|easy|easier|possible|more)\b", 1.2),
    (r"\bthis (?:makes|allows|helps|enables) (?:it|people|visitors|travelers|them|individuals)\b", 0.8),
    (r"\b(?:is|are) (?:widely|internationally|particularly|especially|well|best|also|most) (?:known|famous|recognized|popular|celebrated|associated|loved|regarded)\b", 1.2),
    (r"\b(?:known|famous|celebrated|renowned) for\b", 0.8),
    (r"\battract\w* (?:millions|thousands|large|many|visitors|tourists|hikers|crowds|travelers)\b", 1.2),
    (r"(?:^|(?<=[.!?]\s))(?:Overall|Ultimately|In conclusion|In summary|In the end|Looking ahead|In short),", 1.5),
    (r"\bat the same time\b", 0.5),
    (r"\bit is (?:important|essential|crucial|worth noting|necessary|vital) (?:to|that)\b", 1.2),
    (r"\b(?:important|essential|crucial|vital) (?:to|for) (?:\w+ )?(?:understand|maintain|ensure|address|consider|note|recognize|improving)\b", 0.8),
    (r"\b(?:various|numerous|several|diverse|different) (?:types|kinds|aspects|factors|regions|ways|sectors|strategies|forms)\b", 0.5),
    (r"\b(?:continues?|continued) to (?:grow|shape|evolve|attract|play)\b", 0.8),
    (r"\bnot (?:limited|restricted) to\b", 0.8), (r"\bfar more than\b", 1.0),
    (r"\b(?:health|physical|mental|social|economic|cultural|environmental|educational|historical|emotional) (?:and (?:mental|social|economic|cultural|environmental|physical|emotional|psychological|educational|political|historical) )?(?:benefits|well-being|wellbeing|development|impact|significance|importance|stability)\b", 0.8),
    (r"\b(?:challenges|opportunities) (?:and|or) (?:challenges|opportunities|benefits|risks|drawbacks)\b|\bboth (?:challenges|opportunities|benefits|advantages)\b", 1.0),
    (r"\b(?:the )?(?:future|outlook) (?:of [\w\s]{3,30} )?(?:appears|looks|remains|depends|is)\b[^.]{0,30}\b(?:promising|bright|uncertain|depend\w*)\b", 1.0),
    (r"\bcan (?:also )?(?:help|improve|enhance|reduce|support|lead to|contribute)\b", 0.4),
    (r"\bmany (?:people|travelers|visitors|tourists|experts|individuals|organizations|companies)\b", 0.5),
    (r"\bthese (?:challenges|obstacles|benefits|advantages|strategies)\b", 0.6),
    (r"\b(?:is|are|has|have) (?:also )?(?:become|becoming) (?:increasingly |more and more )?(?:popular|important|common|essential|significant|mainstream|widespread)\b", 1.0),
    (r"\bcan (?:be )?(?:a )?(?:challenging|rewarding|daunting|overwhelming)\b", 0.6),
    (r"\bby (?:prioritizing|adopting|combining|balancing|using|choosing|supporting|investing|understanding)\b", 0.6),
    (r"\bsuch as\b", 0.25), (r"\bincluding\b", 0.2), (r"\bbeyond\b", 0.3), (r"(?:^|(?<=[.!?]\s))However,", 0.4),
    (r"\bthrough(?:out)? (?:the|its|their)\b", 0.1),
]
# الأنماط العربية تُكتب بحروفها الأصلية ثم تُطبَّع (أ/إ/آ→ا، ة→ه، ى→ي) مثل النص عند المطابقة
GEN_AR = [
    (r"(?:تعد|يعد|تُعد|يُعد) (?:من )?(?:أهم|أبرز|أكثر|أحد|احد|اهم|ابرز)", 1.2),
    (r"(?:مجموعة|عدد) (?:واسعة|كبير|متنوعة|من)", 0.8), (r"\bتسهم في\b|\bيسهم في\b|\bتساهم في\b|\bيساهم في\b|\bتسهم\b", 0.9),
    (r"\bلا (?:يقتصر|تقتصر)\b[^.؟!]{0,60}\b(?:فحسب|فقط)\b", 1.5), (r"\bبل (?:يمتد|تمتد)\b", 1.0),
    (r"\bومن ناحية أخرى\b|\bمن ناحية أخرى\b", 1.0), (r"\bولذلك\b|\bلذلك\b", 0.4), (r"\bومع ذلك\b|\bإلا أن\b", 0.5),
    (r"\bوخلاصة القول\b|\bوختامًا\b|\bوختاما\b|\bوفي الختام\b|\bفي الختام\b|\bويمكن القول\b|\bيمكن القول\b", 1.5),
    (r"\bضرورة ملحة\b|\bحيوي\b|\bحيويًا\b", 0.9), (r"\bعلى حد سواء\b", 1.0), (r"\bيعود بالنفع\b|\bيعود بالفائدة\b", 1.2),
    (r"\bإضافة إلى ذلك\b|\bعلاوة على ذلك\b|\bبالإضافة إلى ذلك\b", 0.8), (r"\bومن ثم\b", 0.5),
    (r"\b(?:يتطلب|تتطلب) ذلك\b", 0.8), (r"\bتحقيق التوازن\b|\bالتوازن بين\b", 0.8), (r"\bالعديد من\b", 0.3),
    (r"\bغني(?:ة)? (?:بـ|ب)\b|\bتراثًا (?:حضاريًا )?غنيًا\b|\bتنوعًا\b|\bفريد\b|\bمتنوعة\b", 0.5), (r"\bيعتمد (?:نجاح|على)\b", 0.5),
    (r"\bتتميز\b|\bيتميز\b", 0.6), (r"\bالجوانب (?:الاقتصادية|الاجتماعية|الثقافية)\b", 0.8), (r"\bمن المهم\b|\bمن الضروري\b", 0.8),
    (r"\bفي العصر الحديث\b|\bفي السنوات الأخيرة\b|\bفي الآونة الأخيرة\b", 0.8), (r"\bتحديات\b", 0.4),
]
RX_GEN = [(re.compile(p, re.I), w, False) for p, w in GEN_EN] + \
         [(re.compile(p.translate(NORM_AR)), w, True) for p, w in GEN_AR]

# إطار المقال: افتتاحية تعريفية عامة + خاتمة تلخيصية (يتكرر في المقالات المولَّدة)
RX_FRAME_OPEN = re.compile(
    r"\b(?:is|are|has become|have become|remains?|continues? to be|has transformed|have transformed|has changed|is rapidly)\b[^.]{0,60}?"
    r"\b(?:one of the|most (?:important|popular|fascinating|attractive|influential|pressing|significant)|far more than|"
    r"an? (?:essential|vital|crucial|key|major|significant|important|remarkable|fascinating|powerful|global|rewarding|fundamental|pressing|popular|captivating)|"
    r"(?:transformed|changed|shaped|reshaped) the way|a country (?:that|where|of)|a (?:city|region|place) (?:that|where|of))\b", re.I)
RX_FRAME_CLOSE = re.compile(
    r"(?:^|(?<=[.!?]\s))(?:Overall|Ultimately|In conclusion|In summary|In the end|Looking ahead|In short|Whether|With (?:patience|careful|the right|proper)|"
    r"By (?:prioritizing|balancing|using|combining|adopting|choosing)|As (?:costs|technology|awareness|infrastructure))\b|"
    r"\b(?:will depend on|valuable lessons|encourages? (?:many|people)|something for (?:every|everyone)|offers? (?:an? )?(?:unforgettable|rich|wide|memorable))\b", re.I)
RX_FRAME_OPEN_AR = re.compile(("(?:تعد|يعد|تُعد|يُعد|يشهد|تشهد) [^.؟!]{0,50}?(?:أهم|أبرز|اهم|ابرز|تحولات|تطورا)").translate(NORM_AR))
RX_FRAME_CLOSE_AR = re.compile(("(?:وخلاصة القول|وختامًا|وختاما|وفي الختام|في الختام|ويمكن القول|وفي النهاية)").translate(NORM_AR))
# صوت شخصي إضافي (ضمائر المخاطب/الجمع + عربي) لمؤشر «غياب الصوت»
RX_VOICE2 = re.compile(r"(?<![\w'’])(?:we|our|ours|us|you|your|yours)(?![\w'’])|(?<![\w])(?:أنا|انا|نحن|رأيي|رايي|أعتقد|اعتقد|أرى|ارى|برأيي|شخصيًا|شخصيا|تجربتي|تجربتنا)(?![\w])", re.I)
RX_RARE_PUNCT = re.compile("[()\"“”«»:;?!—–\\[\\]؟؛]")
RX_MD_STRONG = re.compile(r"\*\*[^*\n]{1,80}\*\*|^\s{0,3}#{1,6}\s+\S|^\s*-{3,}\s*$|`[^`\n]+`", re.M)
RX_MD_BULLET = re.compile(r"^\s*(?:[-*•]|\d+\.)\s+\S", re.M)


# ════════════════════════════════════════════════════════════════════════════
#  قراءة ملفات Word (بدون مكتبات خارجية)
# ════════════════════════════════════════════════════════════════════════════
WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def q(tag: str) -> str:
    return "{%s}%s" % (WNS, tag)


def _int(v):
    try:
        return int(v)
    except Exception:
        return None


def read_rpr(rpr) -> dict:
    d = {}
    if rpr is None:
        return d
    rf = rpr.find(q("rFonts"))
    if rf is not None:
        a = rf.get(q("ascii")) or rf.get(q("hAnsi")) or (("theme:" + rf.get(q("asciiTheme"))) if rf.get(q("asciiTheme")) else None)
        c = rf.get(q("cs")) or (("theme:" + rf.get(q("cstheme"))) if rf.get(q("cstheme")) else None)
        if a:
            d["font"] = a
        if c:
            d["cs_font"] = c
    for tag, key in (("sz", "size"), ("szCs", "cs_size")):
        el = rpr.find(q(tag))
        if el is not None and _int(el.get(q("val"))) is not None:
            d[key] = _int(el.get(q("val"))) / 2.0
    for tag, key in (("b", "bold"), ("i", "italic")):
        el = rpr.find(q(tag))
        if el is not None:
            d[key] = el.get(q("val")) not in ("0", "false", "off")
    return d


def read_ppr(ppr) -> dict:
    d = {}
    if ppr is None:
        return d
    ps = ppr.find(q("pStyle"))
    if ps is not None:
        d["pstyle"] = ps.get(q("val"))
    jc = ppr.find(q("jc"))
    if jc is not None:
        d["align"] = jc.get(q("val"))
    sp = ppr.find(q("spacing"))
    if sp is not None:
        for k in ("after", "before", "line"):
            if _int(sp.get(q(k))) is not None:
                d[k] = _int(sp.get(q(k)))
    ind = ppr.find(q("ind"))
    if ind is not None:
        fl = _int(ind.get(q("firstLine")))
        hg = _int(ind.get(q("hanging")))
        if fl is not None:
            d["first"] = fl
        elif hg is not None:
            d["first"] = -hg
    if ppr.find(q("numPr")) is not None:
        d["numbered"] = True
    return d


def parse_styles(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    defaults = {}
    dd = root.find(q("docDefaults"))
    if dd is not None:
        r = dd.find(q("rPrDefault"))
        if r is not None:
            defaults.update(read_rpr(r.find(q("rPr"))))
        pp = dd.find(q("pPrDefault"))
        if pp is not None:
            defaults.update(read_ppr(pp.find(q("pPr"))))
    styles, default_pstyle = {}, None
    for s in root.findall(q("style")):
        sid = s.get(q("styleId"))
        nm = s.find(q("name"))
        based = s.find(q("basedOn"))
        d = {"name": nm.get(q("val")) if nm is not None else "", "based": based.get(q("val")) if based is not None else None}
        d.update(read_rpr(s.find(q("rPr"))))
        d.update(read_ppr(s.find(q("pPr"))))
        styles[sid] = d
        if s.get(q("type")) == "paragraph" and s.get(q("default")) in ("1", "true"):
            default_pstyle = sid
    return styles, defaults, default_pstyle


def style_get(styles, sid, key):
    n = 0
    while sid and n < 12:
        s = styles.get(sid)
        if not s:
            return None
        if key in s:
            return s[key]
        sid = s.get("based")
        n += 1
    return None


def _norm_align(a):
    return {"start": "left", "left": "left", "end": "right", "right": "right", "both": "both",
            "distribute": "both", "center": "center", None: "left"}.get(a, a)


def _read_txt(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp1256", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


def parse_docx(path: str):
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    doc_bytes = z.read("word/document.xml")
    doc_text = doc_bytes.decode("utf-8", "ignore")
    root = ET.fromstring(doc_bytes)
    styles, defaults, default_pstyle = ({}, {}, None)
    if "word/styles.xml" in names:
        styles, defaults, default_pstyle = parse_styles(z.read("word/styles.xml"))
    body = root.find(q("body"))
    paras: List[Para] = []

    def handle_p(p, in_table):
        pd = read_ppr(p.find(q("pPr")))
        sid = pd.get("pstyle") or default_pstyle

        def pget(key, dflt=None):
            if key in pd:
                return pd[key]
            v = style_get(styles, sid, key)
            if v is not None:
                return v
            return defaults.get(key, dflt)

        sname = (styles.get(sid, {}).get("name") or "").lower()
        para = Para(in_table=in_table, style_name=sname)
        para.is_heading = sname.startswith(("heading", "title", "subtitle", "toc heading")) or sname.startswith("عنوان")
        para.is_list = bool(pget("numbered", False)) or "list" in sname
        para.align = _norm_align(pget("align"))
        para.after, para.before, para.line = pget("after"), pget("before"), pget("line")
        para.first = pget("first", 0) or 0
        para.has_drawing = p.find(".//" + q("drawing")) is not None or p.find(".//" + q("pict")) is not None
        pos = 0
        chunks = []
        for r in p.iter(q("r")):
            parts = []
            for ch in r:
                tg = ch.tag
                if tg == q("t"):
                    parts.append(ch.text or "")
                elif tg == q("tab"):
                    parts.append("\t")
                elif tg in (q("br"), q("cr")):
                    if ch.get(q("type")) in ("page", "column"):
                        continue
                    parts.append(" ")
                    para.hard_breaks += 1
                elif tg == q("noBreakHyphen"):
                    parts.append("-")
            s = "".join(parts)
            if not s:
                continue
            rd = read_rpr(r.find(q("rPr")))
            arabic = bool(AR_RE.search(s))

            def rget(key):
                if key in rd:
                    return rd[key]
                v = style_get(styles, sid, key)
                return v if v is not None else defaults.get(key)

            font = rget("cs_font") if arabic else rget("font")
            size = (rget("cs_size") if arabic else None) or rget("size")
            para.runs.append((pos, pos + len(s), font or "افتراضي", size or 0, bool(rget("bold")), bool(rget("italic"))))
            chunks.append(s)
            pos += len(s)
        para.text = "".join(chunks)
        paras.append(para)

    if body is not None:
        for child in body:
            if child.tag == q("p"):
                handle_p(child, False)
            elif child.tag == q("tbl"):
                for p in child.iter(q("p")):
                    handle_p(p, True)
            elif child.tag == q("sdt"):
                for p in child.iter(q("p")):
                    handle_p(p, False)

    # بيانات الملف
    def tag(xml, name):
        mm = re.search(r"<(?:\w+:)?%s[^>]*>([^<]*)</" % name, xml)
        return mm.group(1).strip() if mm else None

    core = z.read("docProps/core.xml").decode("utf-8", "ignore") if "docProps/core.xml" in names else ""
    app = z.read("docProps/app.xml").decode("utf-8", "ignore") if "docProps/app.xml" in names else ""
    sett = z.read("word/settings.xml").decode("utf-8", "ignore") if "word/settings.xml" in names else ""
    meta = {
        "creator": tag(core, "creator"), "last_modified_by": tag(core, "lastModifiedBy"),
        "revision": tag(core, "revision"), "created": tag(core, "created"), "modified": tag(core, "modified"),
        "total_time": tag(app, "TotalTime"), "application": tag(app, "Application"),
        "rsid_sessions": str(len(re.findall(r"<w:rsid\s", sett))),
        "rsid_used": str(len(set(re.findall(r'w:rsid\w*="([0-9A-Fa-f]{8})"', doc_text)))),
        "tracked": str(len(re.findall(r"<w:(?:ins|del)\s", doc_text))),
        "comments": "نعم" if "word/comments.xml" in names else "لا",
        "drawings": str(len(re.findall(r"<w:drawing[\s>]|<w:pict[\s>]", doc_text))),
        "media": str(len([n for n in names if n.startswith("word/media/")])),
    }
    z.close()
    return paras, meta


# ════════════════════════════════════════════════════════════════════════════
#  أدوات عامة: تقطيع الجمل، أدوار الفقرات، الإحالات
# ════════════════════════════════════════════════════════════════════════════
def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


ABBREV_ALL = set(ABBREV) | {"et", "al", "ed", "eds", "dept", "approx", "ca", "jr", "sr", "inc", "ltd", "co",
                            "u.s", "u.k", "ph.d", "e.g", "i.e", "etc", "vs", "cf", "pp", "vol", "no", "fig", "figs"}
_SENT_END = re.compile(r"([.!?؟…]+)([\"'”’)\]»]*)(\s+)")


def split_sentences(t: str):
    """يعيد قائمة (بداية، نهاية) لكل جملة مع حماية الاختصارات والأعداد."""
    spans, start = [], 0
    for m in _SENT_END.finditer(t):
        nxt = t[m.end():m.end() + 1]
        if not nxt:
            continue
        if m.group(1) == ".":
            m2 = re.search(r"([^\s(\[“\"'«]+)$", t[start:m.start(1)])
            tok = m2.group(1) if m2 else ""
            if tok.lower().strip(".") in ABBREV_ALL or re.fullmatch(r"[A-Za-z]", tok):
                continue
            if tok.isdigit() and nxt.isdigit():
                continue
            if nxt.islower():
                continue
        s, e = start, m.end(2)
        while s < e and t[s].isspace():
            s += 1
        if e > s:
            spans.append((s, e))
        start = m.end()
    s, e = start, len(t.rstrip())
    while s < e and t[s].isspace():
        s += 1
    if e > s:
        spans.append((s, e))
    return spans


RX_CAPTION = re.compile(r"^\s*(?:Table|Figure|Fig\.?|Chart|Graph|Diagram|Equation|Scheme|جدول|الجدول|شكل|الشكل|رسم|مخطط)\s*\(?(\d+)\)?\s*[.:\-–—)]?\s", re.I)
RX_REFHEAD = re.compile(r"^\s*(?:\d+[.)]?\s*)?(?:references?|bibliography|works\s+cited|literature\s+cited|sources|المراجع|المصادر|قائمة\s+المراجع|المراجع\s+والمصادر|مراجع|المصادر\s+والمراجع)\s*:?\s*$", re.I)
RX_KEYWORDS = re.compile(r"^\s*(?:key\s*words?|index\s+terms|الكلمات\s+المفتاحية|الكلمات\s+الدالة)\s*[:：]", re.I)
RX_ABSTRACT = re.compile(r"^\s*(?:\d+[.)]?\s*)?(?:abstract|summary|ملخص|المستخلص|الملخص)\s*:?\s*$", re.I)
RX_CONCL = re.compile(r"conclusion|recommendation|خاتمة|الخلاصة|الاستنتاج|التوصيات|الخاتمة", re.I)
RX_NUMHEAD = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+\S")


def assign_roles(paras: List[Para], docx: bool):
    if not docx:     # نص ملصق: استنتاج العناوين من الشكل
        n = len(paras)
        for i, p in enumerate(paras):
            t = p.text.strip()
            if not t:
                continue
            if re.match(r"^#{1,6}\s+\S", t):
                p.is_heading = True
                continue
            nw = len(WORD_RE.findall(t))
            if 1 <= nw <= 10 and len(t) <= 90 and t[-1] not in ".!?؟،,;:" and not RX_CAPTION.match(t):
                prev_blank = i == 0 or not paras[i - 1].text.strip()
                nxt = next((q for q in paras[i + 1:] if q.text.strip()), None)
                if prev_blank and nxt is not None and len(WORD_RE.findall(nxt.text)) >= 10:
                    p.is_heading = True
    in_refs, seen_head = False, False
    for p in paras:
        t = p.text.strip()
        if not t:
            p.role = "empty"
            continue
        if p.is_heading or (RX_REFHEAD.match(t) and len(t) < 40):
            seen_head = True
            p.is_heading = True
            p.role = "heading"
            in_refs = bool(RX_REFHEAD.match(t))
            continue
        if in_refs:
            p.role = "reference"
            continue
        if p.in_table:
            p.role = "table"
        elif RX_CAPTION.match(t) and len(t) < 400:
            p.role = "caption"
        elif RX_KEYWORDS.match(t):
            p.role = "keywords"
        elif p.is_list:
            p.role = "list"
        elif docx and not seen_head and len(WORD_RE.findall(t)) < 25:
            p.role = "front"
        else:
            p.role = "body"


PROSE_ROLES = ("body", "list", "table", "caption", "keywords")

# ── الإحالات داخل المتن ──
RX_CITE_AY = re.compile(r"\b([A-Z][\w'’\-]+)(?:\s+(?:et\s+al\.?|and\s+[A-Z][\w'’\-]+|&\s+[A-Z][\w'’\-]+))?,?\s*\(?((?:19|20)\d\d)[a-z]?\b")
RX_CITE_NUM = re.compile(r"\[\s*(\d+(?:\s*[,–\-]\s*\d+)*)\s*\]")
RX_CITE_AR = re.compile(r"\(\s*([\u0600-\u06FF]+)(?:\s+[\u0600-\u06FF]+){0,3}\s*[،,]?\s*((?:19|20)\d\d)\s*\)")


def has_citation(s: str) -> bool:
    return bool(RX_CITE_AY.search(s) or RX_CITE_NUM.search(s) or RX_CITE_AR.search(s))


def _expand_nums(g: str):
    out = set()
    for part in re.split(r"\s*,\s*", g):
        m = re.match(r"(\d+)\s*[–\-]\s*(\d+)$", part.strip())
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < b - a < 60:
                out.update(range(a, b + 1))
        elif part.strip().isdigit():
            out.add(int(part.strip()))
    return out


def analyze_citations(paras: List[Para], sink: "_Sink"):
    """سلامة الإحالات: هل كل مرجع في القائمة مذكور فعلًا داخل المتن؟"""
    refs = [(i, p) for i, p in enumerate(paras) if p.role == "reference" and len(p.text.strip()) >= 25]
    cites_ay, cites_num, n_intext = set(), set(), 0
    for p in paras:
        if p.role in ("reference", "heading", "front", "empty"):
            continue
        for m in RX_CITE_AY.finditer(p.text):
            cites_ay.add((m.group(1).lower(), m.group(2)))
            cites_ay.add((m.group(1).lower(), None))
            n_intext += 1
        for m in RX_CITE_AR.finditer(p.text):
            cites_ay.add((m.group(1), m.group(2)))
            cites_ay.add((m.group(1), None))
            n_intext += 1
        for m in RX_CITE_NUM.finditer(p.text):
            cites_num |= _expand_nums(m.group(1))
            n_intext += 1
    ctx = {"n_refs": len(refs), "n_intext": n_intext, "uncited": 0}
    if len(refs) < 3:
        return 0.0, ctx
    uncited = 0
    for idx, (i, p) in enumerate(refs):
        t = p.text.strip()
        mnum = re.match(r"^\[?(\d{1,3})[\].)]\s", t)
        if mnum:
            ok = int(mnum.group(1)) in cites_num
        else:
            fm = re.match(r"^\W*([^\s,.(]+)", t)
            first = fm.group(1).lower() if fm else ""
            ym = re.search(r"\b((?:19|20)\d\d)[a-z]?\b", t)
            year = ym.group(1) if ym else None
            acr = re.search(r"\(([A-Z]{2,8})\)", t)
            ok = (first, year) in cites_ay or (year is None and (first, None) in cites_ay)
            if not ok and acr:
                a = acr.group(1).lower()
                ok = (a, year) in cites_ay or (year is None and (a, None) in cites_ay)
        if not ok:
            uncited += 1
            sink.add("ev_uncited", i, t, 0, min(len(t), 60), "لا إحالة داخل المتن لهذا المرجع")
    ctx["uncited"] = uncited
    ratio = uncited / len(refs)
    if n_intext == 0:
        val = 1.0
        hp = next((i for i, p in enumerate(paras) if p.role == "heading" and RX_REFHEAD.match(p.text.strip())), refs[0][0])
        sink.add("ev_nocite", hp, paras[hp].text, 0, len(paras[hp].text), "%d مرجعًا بلا أي إحالة داخل المتن" % len(refs))
    else:
        val = _clamp((ratio - 0.15) / 0.6)
    return val, ctx


def analyze_captions(paras: List[Para], sink: "_Sink", n_drawings: Optional[int]):
    caps = [(i, p) for i, p in enumerate(paras) if p.role == "caption"]
    if len(caps) < 2:
        return 0.0, {"captions": len(caps), "cap_orphans": 0}
    body = "\n".join(p.text for p in paras if p.role in ("body", "list", "front", "keywords"))
    orphans, fig_caps = 0, 0
    for i, p in caps:
        m = RX_CAPTION.match(p.text)
        num = m.group(1)
        label = p.text.strip().split()[0].lower().rstrip(".:")
        is_fig = label.startswith(("fig", "شكل", "الشكل", "chart", "graph", "diagram", "رسم", "مخطط"))
        is_tab = label.startswith(("tab", "جدول", "الجدول"))
        if is_fig:
            fig_caps += 1
            rx = re.compile(r"(?:Figure|Fig\.?|الشكل|شكل|Chart|Graph|Diagram)\s*\(?%s\)?(?!\d)" % num, re.I)
        else:
            rx = re.compile(r"(?:Table|Tab\.?|الجدول|جدول)\s*\(?%s\)?(?!\d)" % num, re.I)
        if not rx.search(body):
            orphans += 1
            sink.add("ev_caption", i, p.text, 0, min(len(p.text), 40), "التسمية لا يُشار إليها في المتن (لا «%s %s» في النص)" % (label, num))
    missing_img = 0
    if n_drawings is not None and fig_caps > n_drawings:
        missing_img = fig_caps - n_drawings
        for i, p in caps:
            lab = p.text.strip().split()[0].lower()
            if lab.startswith(("fig", "شكل", "الشكل")):
                sink.add("ev_caption", i, p.text, 0, min(len(p.text), 40),
                         "تسمية شكل لكن الملف لا يحوي صورة/رسمًا فعليًا (%d صورة مقابل %d تسمية)" % (n_drawings, fig_caps))
                break
    val = _clamp((orphans + 1.5 * missing_img) / max(1.0, len(caps)))
    return val, {"captions": len(caps), "cap_orphans": orphans, "missing_img": missing_img}


def analyze_template(paras: List[Para], cite_ctx: dict):
    heads = [p.text.strip() for p in paras if p.role == "heading"]
    if len(heads) < 4:
        return 0.0, {}
    checks = {}
    checks["abstract"] = any(RX_ABSTRACT.match(h) for h in heads)
    checks["keywords"] = any(p.role == "keywords" for p in paras)
    tops = [int(m.group(1).split(".")[0]) for h in heads for m in [RX_NUMHEAD.match(h)] if m and "." not in m.group(1)]
    subs = [h for h in heads if RX_NUMHEAD.match(h) and "." in RX_NUMHEAD.match(h).group(1)]
    seq = len(tops) >= 5 and tops == list(range(tops[0], tops[0] + len(tops)))
    checks["numbered_seq"] = seq
    checks["subsections"] = len(subs) >= 3 and seq
    checks["conclusion"] = any(RX_CONCL.search(h) for h in heads)
    checks["future_work"] = any(re.search(r"future|مستقبل", h, re.I) for h in heads)
    alltext = " ".join(p.text for p in paras if p.role == "body")
    checks["objectives_enum"] = bool(re.search(r"\(i\)", alltext) and re.search(r"\(ii\)", alltext) and re.search(r"\(iii\)", alltext))
    caps = [p.text for p in paras if p.role == "caption"]
    checks["caption_series"] = len(caps) >= 3 and all(re.match(r"^\s*(?:Table|Figure|جدول|شكل)\s+\d+[.:]", c, re.I) for c in caps)
    checks["refs"] = cite_ctx.get("n_refs", 0) >= 3
    frac = sum(checks.values()) / len(checks)
    return _clamp((frac - 0.45) / 0.45), checks


# ════════════════════════════════════════════════════════════════════════════
#  قوالب الصياغة والمفردات (مؤشرات أسلوبية)
# ════════════════════════════════════════════════════════════════════════════
def _rx(p, f=re.I):
    return re.compile(p, f)

WEAK_PHRASES = {"moreover", "furthermore", "additionally", "not only", "overall,", "in summary", "in conclusion", "to summarize",
                "journey", "comprehensive", "robust", "landscape", "it is worth noting", "it's worth noting", "plays a crucial role",
                "play a crucial role", "crucial role", "foster", "fostering", "leverage", "leveraging", "realm of", "in the realm of",
                "في الختام", "ختاما", "في الخلاصة", "شامل", "متكامل", "مشهد", "رحلة", "بصفه عامه", "يمكن القول", "بالاضافه الي ذلك",
                "علاوه علي ذلك", "كما", "في سياق", "على صعيد"}

CONTRAST_PATTERNS = [
    (_rx(r"\bnot\s+(?:merely|only|just|simply|solely|necessarily)\b[^.;!?]{0,140}?\b(?:but|rather|instead)\b"), 0.6),
    (_rx(r"\b(?:does|do|did|is|are|was|were|can|could)\s+not\s+(?:lie|lies|reside|consist|depend|rely|stem)\s+(?:solely\s+)?(?:in|on|upon)\b[^.;!?]{0,120}?\b(?:but|rather)\b"), 1.0),
    (_rx(r"\b(?:is|are)\s+(?:an?\s+)?[\w\-]+(?:\s[\w\-]+){0,3}\s+(?:problem|task|challenge|question|issue)\s+before\s+it\s+is\b"), 1.3),
    (_rx(r"\bnot\s+(?:an?|the)\s+[^.;!?]{3,90}?[;,]\s*(?:but\s+)?(?:an?|the|it\s+is|they\s+are|rather)\b"), 0.8),
    (_rx(r"\bnot\s+(?:an?|the)\s+[^.;!?]{3,90}?\.\s+(?:It|This|They)\s+(?:is|are)\b"), 0.8),
    (_rx(r"\bbeyond\s+(?:merely|simply|just|mere)\b"), 0.8),
    (_rx(r"\b(?:more|less)\s+about\b[^.;!?]{0,60}\b(?:than|and\s+more)\b"), 0.8),
    (_rx(r"\binstead\s+of\s+(?:merely|simply|just)\b"), 0.8),
    (_rx(r"\brather\s+than\b"), 0.4),
    (_rx(r"\bnot\s+(?:the|an?)\s+[^.;!?]{3,80}\bbut\s+(?:the|an?|one)\b"), 0.7),
    # عربي (على النص بعد التطبيع)
    (_rx(r"\bلا\s+(?:يقتصر|تقتصر|يكمن|تكمن|يعتمد|تعتمد|يتوقف|يتوقف)\b[^.؟!]{0,90}?(?:بل|وانما|ولكن|لكن)\b"), 1.0),
    (_rx(r"\bليس[ت]?\s+[^.؟!]{3,60}?(?:بل|وانما)\b"), 0.8),
]

SIGNPOST_PATTERNS = [
    (_rx(r"(?:(?<=[.!?]\s)|^)(?:This|The\s+present)\s+(?:paper|study|article|review|work|research|essay|report)\s+(?:presents?|proposes?|examines?|argues?|aims?|investigates?|explores?|addresses?|introduces?|develops?|reviews?|discusses?|concludes?|recommends?)\b"), 1.0),
    (_rx(r"\bThe\s+(?:central|main|key|core|primary|principal)\s+(?:argument|contribution|finding|idea|claim|message|point|objective|aim|goal)\b"), 1.0),
    (_rx(r"\bThe\s+(?:paper|study)\s+(?:recommends|concludes|argues|proposes|presents)\b"), 1.0),
    (_rx(r"\bThe\s+(?:real|true)\s+(?:engineering\s+)?(?:value|challenge|test|power)\b"), 0.9),
    (_rx(r"\bIn\s+this\s+(?:sense|way|regard|respect|context)\b"), 0.7),
    (_rx(r"\bTaken\s+together\b"), 0.8),
    (_rx(r"\bFrom\s+(?:an?|the)\s+[\w\- ]{3,45}\s+(?:perspective|standpoint|viewpoint)\b"), 0.7),
    (_rx(r"\bIt\s+(?:should|must)\s+be\s+(?:noted|emphasi[sz]ed|stressed)\b"), 0.8),
    (_rx(r"\bThis\s+(?:shift|distinction|principle|transparency|emphasis|approach|integration|design)\s+(?:expands|supports|allows|highlights|underscores|reflects|reinforces|means|makes|ensures|enables|requires)\b"), 0.7),
    (_rx(r"\bThe\s+(?:remainder|rest)\s+of\s+(?:the|this)\s+(?:paper|article)\s+is\b"), 0.8),
    (_rx(r"\bis\s+(?:central|critical|essential|fundamental|vital)\s+(?:to|for)\b"), 0.5),
    (_rx(r"\b(?:the\s+)?table\s+emphasi[sz]es\b"), 0.8),
    (_rx(r"\bUltimately,"), 0.6),
    (_rx(r"\bThese\s+(?:findings|results|advantages|considerations)\s+(?:support|suggest|underscore|highlight)\b"), 0.7),
    (_rx(r"\bانطلاقا\s+من\s+(?:ذلك|هذا)\b"), 0.6),
    (_rx(r"\bوتاتي\s+اهميه\b|\bتبرز\s+اهميه\b|\bمن\s+هنا\s+تبرز\b"), 0.7),
]

VAGUE_EN = _rx(
    r"\b(?:recent|current|existing|previous|prior|emerging|growing|extensive)\s+(?:literature|research|studies|work|evidence|reviews?|findings)\b"
    r"|\b(?:studies|research|reviews?|literature|evidence|findings|reports|analyses|experts|scholars|researchers)\b(?:\s+\S+){0,6}?\s+(?:consistently\s+|clearly\s+|generally\s+|widely\s+|increasingly\s+)?(?:show|indicate|suggest|emphasi[sz]e|highlight|report|demonstrate|reveal|confirm|agree)(?:s|d|ed)?\b"
    r"|\bit\s+(?:has\s+been|is)\s+(?:widely\s+|well\s+)?(?:shown|reported|argued|established|recognized|recognised|acknowledged|documented)\b"
    r"|\bthe\s+evidence\s+(?:reviewed\s+here\s+)?(?:suggests|indicates|shows)\b"
    r"|\bmany\s+(?:researchers|studies|experts|scholars)\b"
    r"|\baccording\s+to\s+(?:recent|several|many|various)\b")
VAGUE_AR = _rx(
    r"(?:تشير|تؤكد|تظهر|اظهرت|اشارت|اكدت|تجمع|توضح|تبين|اثبتت|كشفت)\s+(?:\S+\s+){0,2}(?:الدراسات|الابحاث|الادبيات|التقارير|الخبراء|النتائج|التجارب)"
    r"|(?:الدراسات|الابحاث|الادبيات)\s+(?:الحديثه|السابقه|المتاحه)")
RX_ORDINAL = _rx(r"(?:^|(?<=[.!?]\s))(?:The\s+)?(?:first|second|third|fourth|fifth|sixth|last|final)\s+(?:is|are|was|were|involves?|step|layer|challenge|limitation|consideration|dimension|component)\b"
                 r"|(?:^|(?<=[.!?]\s))(?:Firstly|Secondly|Thirdly|Fourthly|Lastly|First|Second|Third|Fourth|Finally),"
                 r"|\(\s*(?:i{1,3}|iv|v|vi)\s*\)"
                 r"|(?:^|(?<=[.!?]\s))(?:اولا|ثانيا|ثالثا|رابعا|خامسا)\b")
RX_TRICOLON = _rx(r"\b[\w\-/]+(?:\s[\w\-/]+){0,4},\s+[\w\-/]+(?:\s[\w\-/]+){0,4},?\s+(?:and|or|&)\s+[\w\-/]+")
RX_TRICOLON_AR = _rx(r"[\u0600-\u06FF]+(?:\s[\u0600-\u06FF]+){0,3}\s*،\s*[\u0600-\u06FF]+(?:\s[\u0600-\u06FF]+){0,3}\s*،?\s*و[\u0600-\u06FF]+")
RX_ALT_DASH = re.compile(r"(?<=\w) (?:-|–|--) (?=\w)")

VOCAB_STRONG = (r"delv\w+|tapestr\w+|multifacet\w+|pivotal|holistic\w*|seamless\w*|underscor\w+|leverag\w+|foster\w*|fostering|intricat\w+|"
                r"nuanc\w+|paramount|cornerstone|interplay|realm|myriad|bolster\w*|streamlin\w+|empower\w*|transformativ\w+|"
                r"unprecedented|noteworthy|meticulous\w*|commendable|indispensable|synerg\w+|orchestrat\w+|"
                r"imperative|burgeoning|ever-?evolving|groundbreaking|game-?chang\w+|cutting-?edge|invaluable")
VOCAB_MED = (r"robust\w*|comprehensive\w*|crucial\w*|vital\w*|essential\w*|framework\w*|integrat\w+|enhanc\w+|facilitat\w+|"
             r"ensur\w+|mitigat\w+|optim\w+|scalab\w+|adaptiv\w+|actionable|trustworthy|stakeholders?|tangible|substantial\w*|"
             r"meaningful\w*|notabl\w+|particularly|significantly|demonstrat\w+|highlight\w*|emphasi[sz]\w+|consistent\w*|"
             r"persistent\w*|end-to-end|data-driven|evidence-based|context-appropriate|real-world|interoperab\w+|reproducib\w+|"
             r"interpretab\w+|paradigm\w*|ecosystem\w*|landscape\w*|implications?|dimensions?|resilien\w+|sustainab\w+|"
             r"life-?cycle|trade-?offs?|cyber-?physical|operationaliz\w+|accountab\w+|transparen\w+|reliab\w+|"
             r"noteworthy|alignment|aligned|coherent|rigorous\w*|systematic\w*|critical\w*")
RX_VOC_STRONG = re.compile(r"(?<![\w])(?:%s)(?![\w])" % VOCAB_STRONG, re.I)
RX_VOC_MED = re.compile(r"(?<![\w])(?:%s)(?![\w])" % VOCAB_MED, re.I)

RX_NUMTOK = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?:\s?(?:%|°C|°|percent|kg|g|mg|m³|m3|mm|cm|km|ha|Hz|kHz|MHz|GHz|W|kW|V|mA|ms|min|h|ppm|dS/m|L|MPa|kPa|bar|m|s))?(?![\w])")
RX_YEAR = re.compile(r"^(?:19|20)\d\d$")
RX_VOICE = re.compile(r"(?<![\w'’])(?:I|me|my|mine|myself)(?![\w])|(?<=\w)(?:n['’]t|['’](?:re|ve|ll|m|d))\b|\?|!", re.U)
RX_CAPWORD = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b")


def _spans_of(i, t, sink, kind, rx, note, weight, text_for_match=None):
    """يسجّل كل مطابقة ويعيد مجموع الأوزان."""
    tot = 0.0
    src = text_for_match if text_for_match is not None else t
    for m in rx.finditer(src):
        sink.add(kind, i, t, m.start(), m.end(), note)
        tot += weight
    return tot


def check_style_markers(paras: List[Para], sink: "_Sink", sent_spans: Dict[int, list]):
    """يفحص الفقرات النثرية ويعيد قاموس عدّادات لكل مؤشر + عدّادات لكل فقرة."""
    c = Counter()
    per_para: Dict[int, Counter] = {}
    for i, p in enumerate(paras):
        t = p.text
        if p.role not in PROSE_ROLES or not t.strip():
            continue
        pc = per_para.setdefault(i, Counter())
        nt = t.translate(NORM_AR)

        # عبارات نمطية
        for m in RX_LEXICON.finditer(nt):
            wk = re.sub(r"\s+", " ", m.group(0).lower()) in WEAK_PHRASES
            c["lex"] += 0.25 if wk else 1.0
            pc["lex"] += 0.25 if wk else 1.0
            sink.add("ai_phrase", i, t, m.start(), m.end(), "عبارة نمطية" + (" (ضعيفة الدلالة)" if wk else ""))
        # مفردات (القوي بوزن 1، المتوسط بوزن 0.3) — تُحتسب في المتن والقوائم فقط
        if p.role in ("body", "list"):
            for m in RX_VOC_STRONG.finditer(t):
                c["voc_s"] += 1
                pc["voc_s"] += 1
                sink.add("ai_vocab", i, t, m.start(), m.end(), "مفردة مرتبطة بنصوص النماذج اللغوية")
            nm = len(RX_VOC_MED.findall(t))
            c["voc_m"] += nm
            pc["voc_m"] += nm
        # Markdown
        if p.role != "heading" or re.match(r"^#{1,6}\s", t):
            for m in RX_MD_STRONG.finditer(t):
                c["md"] += 1
                pc["md"] += 1
                sink.add("ai_markdown", i, t, m.start(), m.end(), "رموز Markdown داخل النص")
        if p.role in ("body", "list") and not p.is_heading:
            c["md_bullets"] += len(RX_MD_BULLET.findall(t))
        # الشرطة الطويلة وبدائلها
        for m in re.finditer("—", t):
            c["dash"] += 1.0
            sink.add("ai_emdash", i, t, m.start(), m.end())
        for m in RX_ALT_DASH.finditer(t):
            c["dash"] += 0.6
            sink.add("ai_dash", i, t, m.start(), m.end(), "شرطة قصيرة محاطة بمسافات بديلة عن الشرطة الطويلة")

        if p.role not in ("body", "list", "caption"):
            continue
        # صياغة عامة محايدة
        for rx, w, is_ar in RX_GEN:
            for m in rx.finditer(nt if is_ar else t):
                c["gen"] += w
                pc["gen"] += w
                sink.add("ai_generic", i, t, m.start(), m.end(), "صياغة عامة نمطية")
        # قوالب التقابل
        for rx, w in CONTRAST_PATTERNS:
            tgt = nt if any("\u0600" <= ch <= "\u06ff" for ch in rx.pattern) else t
            for m in rx.finditer(tgt):
                c["contrast"] += w
                pc["contrast"] += w
                sink.add("ai_contrast", i, t, m.start(), m.end(), "قالب تقابلي نمطي")
        # عبارات توجيهية
        for rx, w in SIGNPOST_PATTERNS:
            tgt = nt if any("\u0600" <= ch <= "\u06ff" for ch in rx.pattern) else t
            for m in rx.finditer(tgt):
                c["signpost"] += w
                pc["signpost"] += w
                sink.add("ai_signpost", i, t, m.start(), m.end(), "عبارة توجيهية/تلخيصية نمطية")
        # إسناد غامض (يُستثنى ما يحمل إحالة في جملته)
        for sp in sent_spans.get(i, []):
            s = t[sp[0]:sp[1]]
            if has_citation(s):
                continue
            m = VAGUE_EN.search(s) or VAGUE_AR.search(s.translate(NORM_AR))
            if m:
                c["vague"] += 1
                pc["vague"] += 1
                sink.add("ai_vague", i, t, sp[0] + m.start(), sp[0] + m.end(), "إسناد إلى «دراسات/أدبيات» بلا مصدر محدد")
        # الترتيب الترقيمي
        for m in RX_ORDINAL.finditer(nt):
            c["enum"] += 1
            pc["enum"] += 1
            sink.add("ai_enum", i, t, m.start(), m.end(), "ترتيب ترتيبي نمطي")
        # ثلاثيات متوازنة (تُحسب لكل جملة)
        for sp in sent_spans.get(i, []):
            s = t[sp[0]:sp[1]]
            m = RX_TRICOLON.search(s) or RX_TRICOLON_AR.search(s)
            if m:
                c["tricolon_sents"] += 1
                pc["tricolon"] += 1
                sink.add("ai_tricolon", i, t, sp[0] + m.start(), sp[0] + m.end(), "قائمة من ثلاثة عناصر أو أكثر")
    return c, per_para


def specificity(paras: List[Para]):
    """كثافة التفاصيل المحددة: أرقام وقياسات وأسماء علم ومعادلات (لكل 1000 كلمة)."""
    nums = eq = 0
    caps_all = Counter()
    words = 0
    for p in paras:
        if p.role not in ("body", "list", "table"):
            continue
        t = p.text
        words += len(WORD_RE.findall(t))
        t2 = RX_CITE_AY.sub(" ", t)
        t2 = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s", "", t2)
        for m in RX_NUMTOK.finditer(t2):
            tok = m.group(0).strip()
            if RX_YEAR.match(tok):
                continue
            nums += 1
        eq += t2.count("=") + t2.count("±") + t2.count("×")
        if p.role != "table":
            caps_all.update(w.lower() for w in RX_CAPWORD.findall(t2))
    kw = max(words, 1) / 1000.0
    # أسماء العلم: الاسم الجديد يُحسب 0.3 والمكرَّر 0.05 (قوائم الأسماء لا تعني «أدلة» فعلية)
    caps = sum(caps_all.values())
    caps_eff = 0.3 * len(caps_all) + 0.05 * (caps - len(caps_all))
    return (nums * 1.0 + caps_eff + eq * 1.0) / kw, nums, caps



# ════════════════════════════════════════════════════════════════════════════
#  تحليل التنسيق (docx) — يعتمد أدوار الفقرات فلا يُحسب العنوان/التسمية/المرجع «عدم اتساق»
# ════════════════════════════════════════════════════════════════════════════
def analyze_format(paras: List[Para], sink: _Sink):
    body = [i for i, p in enumerate(paras) if p.is_body]
    if len(body) < 4:
        return 0.0, {}, 0.0
    comps: Dict[str, float] = {}

    items = []
    for i in body:
        p = paras[i]
        for (s, e, font, size, bold, it) in p.runs:
            n = len(p.text[s:e].strip())
            if n:
                items.append((i, s, e, font, size, n))
    total = sum(x[5] for x in items) or 1
    for key_idx, kind, label in (((3, "fmt_font", "الخط"), (4, "fmt_size", "الحجم")) if items else ()):
        cnt = Counter()
        for it in items:
            cnt[it[key_idx]] += it[5]
        dom = cnt.most_common(1)[0][0]
        minority = total - cnt[dom]
        comps[kind] = min(1.0, (minority / total) / 0.12)
        for (i, s, e, font, size, n) in items:
            val = (font, size)[key_idx - 3]
            if val != dom and n >= 3:
                sink.add(kind, i, paras[i].text, s, e, "%s «%s» بدل السائد «%s»" % (label, val, dom))

    def para_minor(kind, keyfn, note, thresh):
        cnt = Counter(keyfn(paras[i]) for i in body)
        dom = cnt.most_common(1)[0][0]
        minor = [i for i in body if keyfn(paras[i]) != dom]
        comps[kind] = min(1.0, (len(minor) / len(body)) / thresh)
        for i in minor:
            t = paras[i].text
            sink.add(kind, i, t, 0, min(len(t), 40), "%s: %s بدل %s" % (note, keyfn(paras[i]), dom))

    para_minor("fmt_align", lambda p: p.align, "المحاذاة", 0.15)
    para_minor("fmt_spacing", lambda p: (p.after or 0, p.before or 0, p.line or 0), "التباعد (بعد، قبل، سطر)", 0.20)
    para_minor("fmt_indent", lambda p: p.first, "المسافة البادئة", 0.20)

    non_table = [i for i, p in enumerate(paras) if not p.in_table]
    empties = [i for i in non_table if not paras[i].text.strip() and not paras[i].has_drawing]
    comps["fmt_empty_para"] = min(1.0, (len(empties) / max(1, len(non_table))) / 0.08)
    for i in empties:
        sink.add("fmt_empty_para", i, "", 0, 0, "فقرة فارغة تُستخدم للتباعد")

    hb = [i for i in non_table if paras[i].hard_breaks and paras[i].role == "body"]
    comps["fmt_hard_break"] = min(1.0, len(hb) / 5.0)
    for i in hb:
        sink.add("fmt_hard_break", i, paras[i].text, 0, min(40, len(paras[i].text)), "Shift+Enter داخل الفقرة")

    fake = []
    for i in non_table:
        p = paras[i]
        t = p.text.strip()
        if p.role in ("heading", "reference", "caption", "keywords", "front") or p.is_list or not (2 <= len(t) <= 80) or t[-1] in ".:!؟?،,;":
            continue
        real = [r for r in p.runs if p.text[r[0]:r[1]].strip()]
        if real and all(r[4] for r in real):
            fake.append(i)
    comps["fmt_fake_heading"] = min(1.0, len(fake) / 3.0)
    for i in fake:
        sink.add("fmt_fake_heading", i, paras[i].text, 0, len(paras[i].text), "عريض يدويًا بلا نمط عنوان")

    S = sum(comps.values())
    incons = 1.0 - math.exp(-0.6 * S)
    perfect = 1.0 if (S < 0.15 and len(body) >= 8) else (_clamp(1.0 - S / 1.5) if len(body) >= 8 else 0.0)
    return incons, comps, perfect


GEN_CREATORS = ("python-docx", "un-named", "unnamed", "openpyxl", "pandoc", "docx4j", "aspose", "apache poi",
                "chatgpt", "openai", "claude", "gemini", "copilot", "anthropic")


def analyze_meta(meta: Dict[str, str], sink: _Sink):
    """يعيد (آثار تحرير بشري، بصمة توليد برمجي)."""
    def num(k, d=None):
        try:
            return float(meta.get(k))
        except Exception:
            return d
    tt = num("total_time")
    rev = num("revision", 1) or 1
    sess = num("rsid_sessions", 0) or 0
    rsu = num("rsid_used", 0) or 0
    tracked = num("tracked", 0) or 0
    comments = meta.get("comments") == "نعم"
    creator = (meta.get("creator") or "").strip()
    lastmod = (meta.get("last_modified_by") or "").strip()
    app = (meta.get("application") or "")
    created, modified = meta.get("created") or "", meta.get("modified") or ""

    gen, why = 0.0, []
    if any(g in creator.lower() for g in GEN_CREATORS) or any(g in lastmod.lower() for g in GEN_CREATORS):
        gen += 0.55
        why.append("المنشئ «%s» أداة توليد برمجي لا مستخدم Word" % (creator or lastmod))
    if created.startswith("2013-12-23"):
        gen += 0.35
        why.append("تاريخ الإنشاء 2013-12-23 هو تاريخ القالب الافتراضي لمكتبة python-docx (ليس تاريخ كتابة حقيقيًا)")
    if created and created == modified and (tt in (0.0, None)) and rev <= 1:
        gen += 0.25
        why.append("تاريخ الإنشاء = تاريخ التعديل، وزمن التحرير 0، ومراجعة واحدة: لم يُحرَّر الملف في Word")
    if not lastmod:
        gen += 0.05
    if sess <= 2 and rsu <= 3 and not tracked and not comments:
        gen += 0.05
    gen = min(1.0, gen)

    edit = min(1.0, (tt or 0) / 30.0)
    s1 = min(1.0, max(0.0, sess - 1) / 15.0)
    s2 = min(1.0, max(0.0, rsu - 2) / 25.0)
    human = 0.3 * edit + 0.25 * max(s1, s2) + 0.15 * min(1.0, max(0.0, rev - 1) / 10.0) + 0.3 * (1.0 if (tracked or comments) else 0.0)
    human *= (1.0 - 0.8 * gen)

    notes = []
    if tt is not None:
        notes.append("زمن التحرير الإجمالي: %d دقيقة" % tt)
    notes.append("عدد مراجعات الحفظ (revision): %d" % rev)
    notes.append("جلسات التحرير (rsid) في الإعدادات: %d — معرّفات rsid المستخدمة: %d" % (sess, rsu))
    if tracked or comments:
        notes.append("تغييرات متعقَّبة: %d — تعليقات: %s" % (tracked, meta.get("comments")))
    for w in why:
        notes.append("⚠ " + w)
    if not why and tt in (0.0, None) and rev <= 1 and sess <= 2:
        notes.append("لا آثار تحرير تُذكر: يتوافق مع لصق نص جاهز ثم حفظه.")
    for n in notes:
        sink.items.append(Finding("meta", -1, 0, 0, n, ""))
    return human, gen


# ════════════════════════════════════════════════════════════════════════════
#  المحرك الرئيسي
# ════════════════════════════════════════════════════════════════════════════
def _sat(x, scale):
    return 1.0 - math.exp(-max(0.0, x) / scale)


def _cv(vals):
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    if m == 0:
        return None
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    return math.sqrt(var) / m


TEMPLATE_AR = {"abstract": "ملخص", "keywords": "كلمات مفتاحية", "numbered_seq": "ترقيم عناوين متسلسل",
               "subsections": "عناوين فرعية 2.1/3.1", "conclusion": "خاتمة/توصيات", "future_work": "أبحاث مستقبلية",
               "objectives_enum": "أهداف مرقّمة (i)…(v)", "caption_series": "تسميات جداول/أشكال متسلسلة", "refs": "قائمة مراجع"}

REASON_TXT = {
    "cite_gap": lambda c: ("قائمة المراجع فيها %d مرجعًا لكن %s" % (c["n_refs"], "لا توجد أي إحالة داخل المتن (لا «Author, 2023» ولا [1]) — وهذا من أقوى علامات النص المولَّد" if c["n_intext"] == 0 else "%d منها غير مذكورة في المتن" % c["uncited"])),
    "caption_orphan": lambda c: "تسميات الجداول/الأشكال (%d) لا يُشار إلى %d منها في النص%s" % (c.get("captions", 0), c.get("cap_orphans", 0), ("، ولا توجد صورة فعلية للشكل" if c.get("missing_img") else "")),
    "meta_gen": lambda c: "بيانات الملف تدل على توليد برمجي لا كتابة في Word (انظر تبويب «بيانات الملف»)",
    "contrast": lambda c: "%d قالبًا تقابليًا نمطيًا من نوع «ليس X بل Y / X قبل أن يكون Y»" % round(c.get("contrast", 0)),
    "vague_attr": lambda c: "%d جملة تستند إلى «دراسات/أدبيات حديثة» دون إحالة إلى مصدر محدد" % c.get("vague", 0),
    "evidence_low": lambda c: "التفاصيل المحددة (أرقام وقياسات وأسماء) نادرة: %.0f لكل 1000 كلمة" % c.get("spec", 0),
    "ai_vocab": lambda c: "مفردات أكاديمية-مولَّدة شائعة (framework, robust, integration…) بكثافة %.0f لكل 1000 كلمة" % c.get("voc_density", 0),
    "ai_lexicon": lambda c: "%d عبارة نمطية شائعة في نصوص النماذج اللغوية" % c.get("lex", 0),
    "generic_phr": lambda c: "صياغة عامة محايدة متكررة بكثافة %.0f لكل 1000 كلمة (المعتاد في الكتابة البشرية أقل من 6) — مثل «one of the most» و«offers a wide range of» و«Whether a traveler is interested in…»" % c.get("gen_density", 0),
    "frame": lambda c: "إطار مقال نمطي: " + " + ".join(x for x, f in (("افتتاحية تعريفية عامة", c.get("frame_open")), ("خاتمة تلخيصية", c.get("frame_close"))) if f),
    "punct_flat": lambda c: "لا يكاد النص يستخدم إلا الفاصلة والنقطة (%.1f علامة نادرة لكل 1000 كلمة؛ في الكتابة البشرية عادةً أكثر من 15)" % c.get("rare_d", 0),
    "no_voice": lambda c: "لا أثر لصوت شخصي في نص طويل (لا ضمائر متكلم/مخاطب ولا أسئلة ولا اختصارات)",
    "signpost": lambda c: "عبارات توجيهية/تلخيصية نمطية (المحور المركزي، في هذا المعنى، تأخذ معًا…) × %d" % round(c.get("signpost", 0)),
    "tricolon": lambda c: "%.0f%% من الجمل تحوي قوائم ثلاثية متوازنة (أ، ب، وج)" % (100 * c.get("tricolon_share", 0)),
    "enumer": lambda c: "ترتيب ترتيبي نمطي (الأول/الثاني/الثالث أو i-ii-iii) × %d" % c.get("enum", 0),
    "burst_low": lambda c: "أطوال الجمل متقاربة جدًا (معامل التباين %.2f)" % c.get("cv_s", 0),
    "sent_uniform": lambda c: "%.0f%% من الجمل بطول 12–32 كلمة بلا جمل قصيرة أو طويلة تُذكر" % (100 * c.get("mid_share", 0)),
    "para_uniform": lambda c: "أطوال الفقرات متقاربة جدًا",
    "template": lambda c: "بنية قالبية مثالية: " + "، ".join(TEMPLATE_AR.get(k, k) for k, v in c.get("template_checks", {}).items() if v),
    "markdown": lambda c: "بقايا تنسيق Markdown داخل النص",
    "emdash": lambda c: "استخدام الشرطة الطويلة/بدائلها بكثرة",
    "openers": lambda c: "كثرة افتتاح الجمل بأدوات ربط نمطية",
    "fmt_perfect": lambda c: "تنسيق مثالي الاتساق بلا أي أثر لتحرير يدوي",
    "clean": lambda c: "النص نظيف تمامًا من الأخطاء الطباعية (ليس بدليل وحده)",
    "err_density": lambda c: "أخطاء مسافات/ترقيم/إملاء بشرية الطابع (%.1f لكل 1000 كلمة)" % c.get("d_err", 0),
    "fmt_incons": lambda c: "عدم اتساق تنسيق يدوي داخل المتن",
    "meta_human": lambda c: "آثار تحرير بشري حقيقية في ملف Word",
    "mixed_conv": lambda c: "خلط في أنماط الأرقام/الاقتباس",
    "ortho": lambda c: "أخطاء إملائية عربية شائعة",
    "voice": lambda c: "صوت شخصي (ضمير المتكلم/اختصارات/أسئلة)",
}


def core_analyze(paras: List[Para], meta: Optional[Dict[str, str]] = None, source: str = "") -> Report:
    W = load_weights()
    is_docx = meta is not None
    assign_roles(paras, is_docx)
    texts = [p.text for p in paras]
    sink = _Sink()
    for i, (t, p) in enumerate(zip(texts, paras)):
        check_paragraph(i, t, p, sink)
    check_conventions([t if p.role != "reference" else "" for t, p in zip(texts, paras)], sink)

    prose_words = sum(len(WORD_RE.findall(p.text)) for p in paras if p.role in PROSE_ROLES)
    words = sum(len(WORD_RE.findall(t)) for t in texts)
    kw = max(prose_words, 1) / 1000.0

    # ── الجمل ──
    sent_spans = {i: split_sentences(p.text) for i, p in enumerate(paras) if p.role in PROSE_ROLES and p.text.strip()}
    st, ctx = check_style_markers(paras, sink, sent_spans)

    sent_lens, openers, nsent = [], 0, 0
    body_sents = 0
    first_words = Counter()
    for i, p in enumerate(paras):
        if p.role != "body":
            continue
        for (a, b) in sent_spans.get(i, []):
            s = p.text[a:b]
            ws = WORD_RE.findall(s)
            if not ws:
                continue
            nsent += 1
            sent_lens.append(len(ws))
            w0 = ws[0].lower().translate(NORM_AR)
            w01 = " ".join(x.lower() for x in ws[:2])
            first_words[w0] += 1
            if w0 in OPENERS or w01 in OPENER_PAIRS:
                openers += 1
    para_words = [len(WORD_RE.findall(p.text)) for p in paras if p.role == "body" and len(WORD_RE.findall(p.text)) >= 15]
    cv_s = _cv(sent_lens) if len(sent_lens) >= 10 else None
    cv_p = _cv(para_words) if len(para_words) >= 4 else None
    mid_share = (sum(1 for n in sent_lens if 12 <= n <= 32) / len(sent_lens)) if len(sent_lens) >= 10 else None

    rep = Report(source=source, texts=texts, words=words, sentences=nsent, is_docx=is_docx)
    fv: Dict[str, float] = {k: 0.0 for k in FEATURES}
    T = TUNE
    ctx_out: Dict[str, float] = {}

    if cv_s is not None:
        fv["burst_low"] = _clamp((T["cv_hi"] - cv_s) / (T["cv_hi"] - T["cv_lo"]))
        ctx_out["cv_s"] = cv_s
    if mid_share is not None:
        fv["sent_uniform"] = _clamp((mid_share - T["mid_lo"]) / (T["mid_hi"] - T["mid_lo"]))
        ctx_out["mid_share"] = mid_share
    if cv_p is not None:
        fv["para_uniform"] = _clamp((T["para_cv_hi"] - cv_p) / (T["para_cv_hi"] - T["para_cv_lo"]))
    voc_w = st["voc_s"] * 1.0 + st["voc_m"] * 0.25
    fv["ai_vocab"] = _sat(voc_w / kw, T["vocab_scale"])
    fv["ai_lexicon"] = _sat(st["lex"] / kw, T["lex_scale"])
    fv["contrast"] = _sat(st["contrast"] / kw, T["contrast_scale"])
    fv["vague_attr"] = _sat(st["vague"] / kw, T["vague_scale"])
    fv["signpost"] = _sat(st["signpost"] / kw, T["signpost_scale"])
    fv["enumer"] = _sat(st["enum"] / kw, 4.0)
    tri_share = (st["tricolon_sents"] / nsent) if nsent >= 8 else 0.0
    fv["tricolon"] = _clamp((tri_share - T["tricolon_lo"]) / (T["tricolon_hi"] - T["tricolon_lo"]))
    fv["markdown"] = _sat(st["md"] + 0.3 * st["md_bullets"], 3.0)
    fv["emdash"] = _sat(st["dash"] / kw, 5.0)
    fv["openers"] = _clamp((openers / nsent) / 0.35) if nsent >= 6 else 0.0

    spec, nnum, ncap = specificity(paras)
    if prose_words >= 150:
        fv["evidence_low"] = _clamp((T["spec_hi"] - spec) / T["spec_hi"])
    # ── (2.1) الصياغة العامة المحايدة ──
    gen_d = st["gen"] / kw
    fv["generic_phr"] = _clamp((gen_d - T["gen_lo"]) / (T["gen_hi"] - T["gen_lo"])) if prose_words >= 120 else 0.0
    # إطار المقال
    body_idx = [i for i, p in enumerate(paras) if p.role == "body" and len(p.text.strip()) >= 40]
    frame_open = frame_close = False
    if body_idx and prose_words >= 150:
        sp0 = sent_spans.get(body_idx[0], [])
        if sp0:
            s0 = paras[body_idx[0]].text[sp0[0][0]:sp0[0][1]]
            frame_open = bool(RX_FRAME_OPEN.search(s0) or RX_FRAME_OPEN_AR.search(s0.translate(NORM_AR)))
        spl = sent_spans.get(body_idx[-1], [])
        if spl:
            tail = paras[body_idx[-1]].text[spl[max(0, len(spl) - 2)][0]:spl[-1][1]]
            frame_close = bool(RX_FRAME_CLOSE.search(tail) or RX_FRAME_CLOSE_AR.search(tail.translate(NORM_AR)))
    fv["frame"] = 0.5 * frame_open + 0.5 * frame_close
    # فقر الترقيم وغياب الصوت (نص طويل فقط، وإلا قد يكون النص قصيرًا بطبيعته)
    rare_n = sum(len(RX_RARE_PUNCT.findall(p.text)) for p in paras if p.role in ("body", "list"))
    rare_d = rare_n / kw
    voice2_n = sum(len(RX_VOICE.findall(p.text)) + len(RX_VOICE2.findall(p.text)) for p in paras if p.role in ("body", "list"))
    if prose_words >= 250:
        fv["punct_flat"] = _clamp((T["punct_hi"] - rare_d) / (T["punct_hi"] - T["punct_lo"]))
        fv["no_voice"] = 1.0 if voice2_n == 0 else 0.0
    ctx_out.update({"gen_density": gen_d, "frame_open": frame_open, "frame_close": frame_close, "rare_d": rare_d})
    ctx_out.update({"spec": spec, "voc_density": voc_w / kw, "lex": st["lex"], "contrast": st["contrast"],
                    "vague": st["vague"], "signpost": st["signpost"], "enum": st["enum"], "tricolon_share": tri_share})

    # ── المراجع والتسميات والقالب ──
    cite_val, cctx = analyze_citations(paras, sink)
    fv["cite_gap"] = cite_val
    ctx_out.update(cctx)
    ndraw = None
    if is_docx:
        try:
            ndraw = int(meta.get("drawings", "0"))
        except Exception:
            ndraw = None
    cap_val, capctx = analyze_captions(paras, sink, ndraw)
    fv["caption_orphan"] = cap_val
    ctx_out.update(capctx)
    tmpl_val, tchecks = analyze_template(paras, cctx)
    fv["template"] = tmpl_val
    ctx_out["template_checks"] = tchecks

    # ── كثافة الأخطاء ──
    err_w = sum(KINDS[k][2] * n for k, n in sink.n.items() if KINDS[k][1] in ERR_CATS)
    d_err = err_w / kw
    conv_n = sum(n for k, n in sink.n.items() if KINDS[k][1] == "convention")
    orth_n = sink.n.get("ar_orth", 0)
    ctx_out["d_err"] = d_err

    f_fmt, comps, f_perfect = 0.0, {}, 0.0
    f_meta, f_gen = 0.0, 0.0
    if is_docx:
        f_fmt, comps, f_perfect = analyze_format(paras, sink)
        f_meta, f_gen = analyze_meta(meta, sink)
    else:
        runs, cur = 0, 0
        for t in texts + ["x"]:
            if not t.strip():
                cur += 1
            else:
                if cur >= 2:
                    runs += 1
                cur = 0
        nonempty = sum(1 for t in texts if t.strip())
        share = runs / max(1, nonempty)
        # نص ملصق قصير أو فيه سطران فارغان في آخره ليس «تنسيقًا يدويًا»: لا يُحتسب إلا مع 6 فقرات فأكثر
        f_fmt = (1.0 - math.exp(-0.6 * min(1.0, share / 0.08))) if nonempty >= 6 else 0.0
        if runs and nonempty >= 6:
            cur = 0
            for i, t in enumerate(texts):
                if not t.strip():
                    cur += 1
                    if cur == 2:
                        sink.add("fmt_empty_para", i, "", 0, 0, "أكثر من سطر فارغ متتالٍ")
                else:
                    cur = 0

    # أخطاء «بشرية» يسهل تزويرها: نحدّ أثرها ولا نمنحها وزنًا كاملًا إلا إن توزعت على النص
    fv["err_density"] = _sat(d_err, 5.0)
    fv["clean"] = math.exp(-d_err / 3.0)
    fv["fmt_incons"] = f_fmt
    fv["fmt_perfect"] = f_perfect
    fv["meta_human"] = f_meta
    fv["meta_gen"] = f_gen
    fv["mixed_conv"] = _sat(conv_n, 4.0)
    fv["ortho"] = _sat(orth_n / kw, 5.0)
    voice_n = 0
    for p in paras:
        if p.role == "body":
            voice_n += len(RX_VOICE.findall(p.text))
    fv["voice"] = _sat(voice_n / kw, 6.0)

    z = W["bias"]
    contribs = {}
    for k in FEATURES:
        c = W[k] * fv[k]
        contribs[k] = (fv[k], W[k], c)
        z += c
    conf = min(1.0, math.sqrt(prose_words / 250.0)) if prose_words else 0.0
    score = 100.0 / (1.0 + math.exp(-z * conf))

    rep.findings = sink.items
    rep.features = contribs
    rep.fvals = fv
    rep.z, rep.confidence, rep.score = z, conf, score
    rep.ctx = ctx_out
    rep.meta = {k: v for k, v in (meta or {}).items() if v}
    rep.stats = {
        "عدد الكلمات": words, "عدد الجمل (المتن)": nsent, "عدد الفقرات": sum(1 for t in texts if t.strip()),
        "متوسط طول الجملة": round(sum(sent_lens) / len(sent_lens), 1) if sent_lens else 0,
        "تباين طول الجمل (CV)": round(cv_s, 2) if cv_s is not None else "-",
        "تباين طول الفقرات (CV)": round(cv_p, 2) if cv_p is not None else "-",
        "كثافة التفاصيل المحددة لكل 1000 كلمة": round(spec, 1),
        "أخطاء مرجّحة لكل 1000 كلمة": round(d_err, 1),
        "عبارات الذكاء الاصطناعي لكل 1000 كلمة": round(st["lex"] / kw, 1),
        "مراجع / إحالات داخل المتن": "%d / %d" % (cctx["n_refs"], cctx["n_intext"]),
    }
    rep.verdict, rep.color = make_verdict(score, prose_words)
    rep.reasons = build_reasons(rep, ctx_out)
    rep.para_scores = paragraph_scores(paras, rep.findings)
    # مستند مختلط: متوسط الدرجة قد يُخفي فقرات مولَّدة داخل نص بشري
    tot_w = sum(n for _, _, n in rep.para_scores)
    hi_w = sum(n for _, sc, n in rep.para_scores if sc >= 55)
    ctx_out["hi_share"] = (hi_w / tot_w) if tot_w else 0.0
    if tot_w >= 150 and ctx_out["hi_share"] >= 0.30 and score < 65:
        rep.verdict += " — مع فقرات مشتبه بها (%.0f%% من النص)" % (100 * ctx_out["hi_share"])
        rep.reasons.insert(0, "⚠ مستند مختلط محتمل: %.0f%% من كلمات المتن في فقرات درجتها ≥ 55 (راجع تبويب «خريطة الفقرات»)" % (100 * ctx_out["hi_share"]))
    return rep


def build_reasons(rep: Report, ctx: dict) -> List[str]:
    ai, hu = [], []
    for k, (v, w, c) in sorted(rep.features.items(), key=lambda kv: -abs(kv[1][2])):
        if abs(c) < 0.2 or k not in REASON_TXT:
            continue
        try:
            txt = REASON_TXT[k](ctx)
        except Exception:
            txt = FEATURE_LABELS[k]
        (ai if c > 0 else hu).append(("%+.1f  " % c) + txt)
    out = []
    if ai:
        out.append("▲ قرائن تدفع نحو الذكاء الاصطناعي:")
        out += ["   " + x for x in ai]
    if hu:
        out.append("▼ قرائن تدفع نحو الكتابة البشرية:")
        out += ["   " + x for x in hu]
    return out


_PARA_W = {"ai_phrase": 1.2, "ai_vocab": 1.0, "ai_contrast": 1.6, "ai_vague": 1.6, "ai_signpost": 1.0,
           "ai_tricolon": 0.7, "ai_enum": 0.7, "ai_markdown": 1.5, "ai_emdash": 0.4, "ai_dash": 0.3, "ai_generic": 0.9}


def paragraph_scores(paras: List[Para], findings: List[Finding]):
    acc = Counter()
    for f in findings:
        if f.para >= 0 and f.kind in _PARA_W:
            acc[f.para] += _PARA_W[f.kind]
    out = []
    for i, p in enumerate(paras):
        if p.role != "body":
            continue
        n = len(WORD_RE.findall(p.text))
        if n < 25:
            continue
        sl = [len(WORD_RE.findall(p.text[a:b])) for a, b in split_sentences(p.text)]
        x = acc[i] / (n / 100.0)
        cvp = _cv(sl) if len(sl) >= 3 else None
        if cvp is not None and cvp < 0.35:
            x += 0.4
        nums = len(RX_NUMTOK.findall(RX_CITE_AY.sub(" ", p.text)))
        caps = len(RX_CAPWORD.findall(p.text))
        if nums + caps == 0:
            x += 0.5
        out.append((i, round(100.0 * _sat(x, 2.2), 0), n))
    return out


def make_verdict(score: float, words: int):
    if words < 40:
        return "النص قصير جدًا للحكم (أقل من 40 كلمة)", "gray"
    if score >= 80:
        return "مولَّد بالذكاء الاصطناعي على الأرجح", "#c92a2a"
    if score >= 65:
        return "يميل بقوة إلى الذكاء الاصطناعي", "#e03131"
    if score >= 52:
        return "يميل إلى الذكاء الاصطناعي", "#e8590c"
    if score >= 40:
        return "غير حاسم", "#f08c00"
    if score >= 25:
        return "يميل إلى الكتابة البشرية", "#5c940d"
    return "غالبًا كتابة بشرية", "#2b8a3e"




def analyze_text(text: str, source: str = "نص ملصق") -> Report:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1].strip():      # أسطر فارغة في آخر/أول النص (من اللصق أو الحفظ) ليست من صياغة الكاتب
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return core_analyze([Para(text=l) for l in lines], None, source)


def analyze_docx(path: str) -> Report:
    paras, meta = parse_docx(path)
    return core_analyze(paras, meta, os.path.basename(path))


def analyze_file(path: str) -> Optional[Report]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return analyze_docx(path)
    if ext in (".txt", ".md", ".text"):
        return analyze_text(_read_txt(path), os.path.basename(path))
    return None


# ════════════════════════════════════════════════════════════════════════════
#  تقارير / معايرة
# ════════════════════════════════════════════════════════════════════════════
def report_to_html(rep: Report) -> str:
    esc = html.escape
    rows = "".join(
        "<tr><td>%s</td><td>%.2f</td><td>%+.2f</td><td style='color:%s'>%+.2f</td></tr>" % (
            esc(FEATURE_LABELS[k]), v, w, "#c92a2a" if c > 0 else "#2b8a3e", c)
        for k, (v, w, c) in sorted(rep.features.items(), key=lambda kv: -abs(kv[1][2])) if v > 0.005)
    cats = "".join("<li>%s: <b>%d</b></li>" % (esc(CAT_LABELS[c]), n) for c, n in rep.count_by_cat().most_common())
    fr = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            esc(f.label), (f.para + 1) if f.para >= 0 else "-", esc(f.text), esc(f.note))
        for f in rep.findings[:1500])
    st = "".join("<li>%s: %s</li>" % (esc(str(k)), esc(str(v))) for k, v in rep.stats.items())
    reasons = "".join("<li style='list-style:none'>%s</li>" % esc(x) for x in rep.reasons) or "<li>-</li>"
    heat = "".join(
        "<tr><td>%d</td><td style='background:%s'>%d</td><td>%d</td><td>%s</td></tr>" % (
            i + 1, "#ffc9c9" if sc >= 55 else ("#ffe8cc" if sc >= 30 else "#ebfbee"), sc, n,
            esc(rep.texts[i][:140]))
        for (i, sc, n) in rep.para_scores)
    return """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><title>تقرير الفحص</title>
<style>body{font-family:Tahoma,Segoe UI,Arial;margin:30px;max-width:1000px}table{border-collapse:collapse;width:100%%}
td,th{border:1px solid #ccc;padding:5px 8px;font-size:13px}th{background:#f1f3f5}h1{margin-bottom:0}
.v{font-size:26px;font-weight:bold;color:%s}</style>
<h1>تقرير فحص النص</h1><p>المصدر: %s — %s</p>
<p class="v">%s — مؤشر الذكاء الاصطناعي: %.0f / 100 (الثقة %.0f%%)</p>
<h3>أهم الأسباب</h3><ul style="padding:0">%s</ul>
<h3>إحصاءات</h3><ul>%s</ul><h3>العوامل المؤثرة في الحكم</h3>
<table><tr><th>العامل</th><th>القيمة (0-1)</th><th>الوزن</th><th>الأثر على الحكم</th></tr>%s</table>
<h3>خريطة الفقرات (درجة الاشتباه لكل فقرة)</h3>
<table><tr><th>الفقرة</th><th>الدرجة</th><th>الكلمات</th><th>بداية الفقرة</th></tr>%s</table>
<h3>ملخص الفئات</h3><ul>%s</ul>
<h3>تفاصيل المواضع المرصودة</h3><table><tr><th>النوع</th><th>الفقرة</th><th>المقتطف</th><th>ملاحظة</th></tr>%s</table>
<p style="color:#868e96;margin-top:30px">تنبيه: النتيجة مؤشر احتمالي مبني على قرائن، وليست دليلًا قاطعًا، ولا يصح الاعتماد عليها وحدها في اتهام أحد.</p>
</html>""" % (rep.color, esc(rep.source), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
              esc(rep.verdict), rep.score, rep.confidence * 100, reasons, st, rows, heat, cats, fr)


HUMAN_SIDE = {"err_density", "fmt_incons", "meta_human", "mixed_conv", "ortho", "voice"}


def calibrate(human_dir: str, ai_dir: str):
    X, Y, names = [], [], []
    for label, d in ((0, human_dir), (1, ai_dir)):
        for fn in sorted(os.listdir(d)):
            try:
                r = analyze_file(os.path.join(d, fn))
            except Exception as e:
                print("تخطي", fn, e)
                continue
            if r and r.words >= 60:
                X.append([r.fvals[k] for k in FEATURES])
                Y.append(label)
                names.append(fn)
    if len(set(Y)) < 2 or len(Y) < 10:
        print("تحتاج 10 عيّنات على الأقل من الفئتين (يفضَّل 50+ لكل فئة).")
        return
    w = [DEFAULT_WEIGHTS[k] for k in FEATURES]
    b = DEFAULT_WEIGHTS["bias"]
    lr, l2, n = 0.3, 0.02, len(Y)
    for _ in range(6000):
        gw, gb = [0.0] * len(w), 0.0
        for x, y in zip(X, Y):
            z = b + sum(wi * xi for wi, xi in zip(w, x))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            e = p - y
            gb += e
            for j, xj in enumerate(x):
                gw[j] += e * xj
        b -= lr * gb / n
        for j in range(len(w)):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
            # قيد الإشارة: العامل «البشري» لا يصير موجبًا، والعامل «الاصطناعي» لا يصير سالبًا
            if FEATURES[j] in HUMAN_SIDE:
                w[j] = min(0.0, w[j])
            else:
                w[j] = max(0.0, w[j])
    correct = 0
    for x, y in zip(X, Y):
        z = b + sum(wi * xi for wi, xi in zip(w, x))
        correct += int((z > 0) == bool(y))
    out = {"_version": VERSION, "bias": b}
    out.update({k: w[i] for i, k in enumerate(FEATURES)})
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("تمت المعايرة على %d عيّنة — الدقة على بيانات التدريب: %.1f%%" % (n, 100.0 * correct / n))
    print("حُفظت الأوزان في:", WEIGHTS_FILE)
    print("احذف weights.json للرجوع إلى الأوزان الافتراضية. قيّم الدقة الحقيقية بعيّنات لم تُستخدم في المعايرة.")


# ════════════════════════════════════════════════════════════════════════════
#  الواجهة الرسومية
# ════════════════════════════════════════════════════════════════════════════
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    CAT_COLORS = {
        "spacing": "#ffd8a8", "punct": "#ffa8a8", "typo": "#d0bfff", "orth": "#eebefa",
        "format": "#a5d8ff", "convention": "#ffec99", "ai": "#b2f2bb",
        "rhetoric": "#96f2d7", "evidence": "#fcc2d7",
    }
    FONT = ("Tahoma", 11)

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("كاشف النصوص المولَّدة بالذكاء الاصطناعي — v" + VERSION)
            self.geometry("1360x820")
            self.minsize(1000, 600)
            self.rep: Optional[Report] = None
            self.docx_path = None
            self.docx_text = None
            self.row_pos = {}
            self._build()

        # ── بناء الواجهة ──
        def _build(self):
            st = ttk.Style(self)
            try:
                st.theme_use("clam")
            except Exception:
                pass
            st.configure("Treeview", font=FONT, rowheight=24)
            st.configure("Treeview.Heading", font=("Tahoma", 10, "bold"))

            bar = ttk.Frame(self, padding=6)
            bar.pack(fill="x")
            for txt, cmd in (("📄 فتح ملف Word / نص", self.open_files), ("📋 لصق من الحافظة", self.paste_clip),
                             ("🔍 تحليل", self.analyze), ("🗑 مسح", self.clear),
                             ("💾 حفظ تقرير HTML", self.export_html), ("📁 تحليل مجلد كامل", self.batch_folder)):
                ttk.Button(bar, text=txt, command=cmd).pack(side="left", padx=3)

            leg = ttk.Frame(self, padding=(8, 0))
            leg.pack(fill="x")
            ttk.Label(leg, text="دليل الألوان: ").pack(side="left")
            for c, col in CAT_COLORS.items():
                tk.Label(leg, text=" %s " % CAT_LABELS[c], bg=col, font=("Tahoma", 9)).pack(side="left", padx=2, pady=2)

            pw = ttk.PanedWindow(self, orient="horizontal")
            pw.pack(fill="both", expand=True, padx=6, pady=6)

            left = ttk.Frame(pw)
            pw.add(left, weight=3)
            ttk.Label(left, text="النص (الصق هنا أو افتح ملفًا):", font=("Tahoma", 10, "bold")).pack(anchor="e")
            tf = ttk.Frame(left)
            tf.pack(fill="both", expand=True)
            self.txt = tk.Text(tf, wrap="word", font=("Tahoma", 13), undo=True, padx=8, pady=6)
            sb = ttk.Scrollbar(tf, command=self.txt.yview)
            self.txt.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.txt.pack(side="left", fill="both", expand=True)
            for c, col in CAT_COLORS.items():
                self.txt.tag_configure(c, background=col)
            self.txt.tag_configure("jump", background="#ffe066", foreground="#000", underline=True)
            self.txt.tag_configure("rtl", justify="right")
            # اختصارات لا تعتمد على لغة لوحة المفاتيح (تعمل مع العربية)
            self.txt.bind("<Control-KeyPress>", self._ctrl_key)
            self.txt.bind("<Shift-Insert>", lambda e: (self.paste_at_cursor(), "break")[1])
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label="لصق", command=self.paste_at_cursor)
            menu.add_command(label="نسخ", command=lambda: self.txt.event_generate("<<Copy>>"))
            menu.add_command(label="قص", command=lambda: self.txt.event_generate("<<Cut>>"))
            menu.add_command(label="تحديد الكل", command=lambda: self.txt.tag_add("sel", "1.0", "end-1c"))
            menu.add_separator()
            menu.add_command(label="تحليل", command=self.analyze)
            self.txt.bind("<Button-3>", lambda e: (self.txt.focus_set(), menu.tk_popup(e.x_root, e.y_root)))

            right = ttk.Frame(pw)
            pw.add(right, weight=2)
            nb = ttk.Notebook(right)
            nb.pack(fill="both", expand=True)

            t1 = ttk.Frame(nb, padding=8)
            nb.add(t1, text="النتيجة")
            self.lbl_verdict = tk.Label(t1, text="— لم يُجرَ تحليل بعد —", font=("Tahoma", 17, "bold"), fg="gray")
            self.lbl_verdict.pack(fill="x")
            self.lbl_score = tk.Label(t1, text="", font=("Tahoma", 11))
            self.lbl_score.pack(fill="x")
            self.cv = tk.Canvas(t1, height=34, highlightthickness=0)
            self.cv.pack(fill="x", pady=4)
            self.lbl_stats = tk.Label(t1, text="", justify="right", anchor="e", font=("Tahoma", 9), fg="#495057")
            self.lbl_stats.pack(fill="x")
            ttk.Label(t1, text="العوامل المؤثرة (موجب = يرفع احتمال الذكاء الاصطناعي):", font=("Tahoma", 10, "bold")).pack(anchor="e", pady=(8, 2))
            self.tr_feat = ttk.Treeview(t1, columns=("v", "c"), show="tree headings", height=8)
            self.tr_feat.heading("#0", text="العامل")
            self.tr_feat.heading("v", text="القيمة")
            self.tr_feat.heading("c", text="الأثر")
            self.tr_feat.column("#0", width=270, anchor="e")
            self.tr_feat.column("v", width=60, anchor="center")
            self.tr_feat.column("c", width=60, anchor="center")
            self.tr_feat.pack(fill="both", expand=True)
            self.tr_feat.tag_configure("ai", foreground="#c92a2a")
            self.tr_feat.tag_configure("hu", foreground="#2b8a3e")
            ttk.Label(t1, text="أهم الأسباب:", font=("Tahoma", 10, "bold")).pack(anchor="e", pady=(8, 2))
            self.txt_reasons = tk.Text(t1, height=9, wrap="word", font=("Tahoma", 10), state="disabled")
            self.txt_reasons.tag_configure("rtl", justify="right")
            self.txt_reasons.tag_configure("ai", foreground="#c92a2a")
            self.txt_reasons.tag_configure("hu", foreground="#2b8a3e")
            self.txt_reasons.pack(fill="both", expand=True)
            tk.Label(t1, text="النتيجة مؤشر احتمالي وليست دليلًا قاطعًا.", fg="#868e96", font=("Tahoma", 9)).pack(fill="x", pady=(6, 0))

            t2 = ttk.Frame(nb, padding=6)
            nb.add(t2, text="المواضع المرصودة")
            top = ttk.Frame(t2)
            top.pack(fill="x")
            self.cmb = ttk.Combobox(top, state="readonly", values=["الكل"] + list(CAT_LABELS.values()), width=22)
            self.cmb.current(0)
            self.cmb.pack(side="right")
            self.cmb.bind("<<ComboboxSelected>>", lambda e: self.fill_findings())
            ttk.Label(top, text="تصفية حسب الفئة:").pack(side="right", padx=4)
            self.tr = ttk.Treeview(t2, columns=("kind", "para", "snip", "note"), show="headings")
            for c, t, w in (("kind", "النوع", 170), ("para", "الفقرة", 50), ("snip", "المقتطف", 170), ("note", "ملاحظة", 160)):
                self.tr.heading(c, text=t)
                self.tr.column(c, width=w, anchor="e")
            vs = ttk.Scrollbar(t2, command=self.tr.yview)
            self.tr.configure(yscrollcommand=vs.set)
            vs.pack(side="left", fill="y")
            self.tr.pack(fill="both", expand=True, pady=4)
            self.tr.bind("<<TreeviewSelect>>", self.on_select)

            t4 = ttk.Frame(nb, padding=6)
            nb.add(t4, text="خريطة الفقرات")
            ttk.Label(t4, text="درجة الاشتباه بالتوليد لكل فقرة (انقر للانتقال إليها):", font=("Tahoma", 10, "bold")).pack(anchor="e")
            self.tr_para = ttk.Treeview(t4, columns=("p", "s", "w", "x"), show="headings")
            for c, t, w in (("p", "الفقرة", 50), ("s", "الدرجة", 60), ("w", "الكلمات", 60), ("x", "بداية الفقرة", 250)):
                self.tr_para.heading(c, text=t)
                self.tr_para.column(c, width=w, anchor="e")
            self.tr_para.tag_configure("hi", background="#ffc9c9")
            self.tr_para.tag_configure("mid", background="#ffe8cc")
            vs4 = ttk.Scrollbar(t4, command=self.tr_para.yview)
            self.tr_para.configure(yscrollcommand=vs4.set)
            vs4.pack(side="left", fill="y")
            self.tr_para.pack(fill="both", expand=True, pady=4)
            self.tr_para.bind("<<TreeviewSelect>>", self.on_para_select)
            self.para_pos = {}

            t3 = ttk.Frame(nb, padding=6)
            nb.add(t3, text="بيانات الملف والملخص")
            self.info = tk.Text(t3, wrap="word", font=("Tahoma", 11), state="disabled", height=10)
            self.info.tag_configure("rtl", justify="right")
            self.info.pack(fill="both", expand=True)

            self.status = ttk.Label(self, text="جاهز.", anchor="e", relief="sunken")
            self.status.pack(fill="x", side="bottom")

        # ── عمليات ──
        def set_status(self, s):
            self.status.config(text=s)
            self.update_idletasks()

        def clear(self):
            self.txt.delete("1.0", "end")
            self.rep = None
            self.docx_path = self.docx_text = None
            self.lbl_verdict.config(text="— لم يُجرَ تحليل بعد —", fg="gray")
            self.lbl_score.config(text="")
            self.lbl_stats.config(text="")
            self.cv.delete("all")
            for t in (self.tr, self.tr_feat, self.tr_para):
                t.delete(*t.get_children())
            self.txt_reasons.config(state="normal")
            self.txt_reasons.delete("1.0", "end")
            self.txt_reasons.config(state="disabled")
            self.set_status("تم المسح.")

        def _get_clip(self):
            for getter in (lambda: self.clipboard_get(),
                           lambda: self.clipboard_get(type="UTF8_STRING"),
                           lambda: self.selection_get(selection="CLIPBOARD")):
                try:
                    s = getter()
                    if s:
                        return s
                except Exception:
                    continue
            return None

        def _ctrl_key(self, e):
            # keycode ثابت للحرف مهما كانت لغة الكيبورد (Windows): V=86 C=67 X=88 A=65
            kc = e.keycode
            if kc == 86:
                self.paste_at_cursor()
                return "break"
            if kc == 67:
                self.txt.event_generate("<<Copy>>")
                return "break"
            if kc == 88:
                self.txt.event_generate("<<Cut>>")
                return "break"
            if kc == 65:
                self.txt.tag_add("sel", "1.0", "end-1c")
                return "break"
            if kc == 90:
                try:
                    self.txt.edit_undo()
                except Exception:
                    pass
                return "break"

        def paste_at_cursor(self):
            s = self._get_clip()
            if not s:
                messagebox.showinfo("الحافظة", "لا يوجد نص في الحافظة. انسخ النص أولًا ثم أعد المحاولة.")
                return
            try:
                if self.txt.tag_ranges("sel"):
                    self.txt.delete("sel.first", "sel.last")
            except Exception:
                pass
            self.txt.insert("insert", s)
            self.txt.see("insert")

        def paste_clip(self):
            s = self._get_clip()
            if not s:
                messagebox.showinfo("الحافظة", "لا يوجد نص في الحافظة. انسخ النص أولًا ثم أعد المحاولة.")
                return
            self.docx_path = self.docx_text = None
            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", s)
            self.analyze()

        def open_files(self):
            paths = filedialog.askopenfilenames(
                title="اختر ملف Word أو نص",
                filetypes=[("Word / نص", "*.docx *.txt *.md"), ("Word", "*.docx"), ("نص", "*.txt *.md")])
            if not paths:
                return
            if len(paths) > 1:
                self.run_batch(list(paths))
                return
            p = paths[0]
            try:
                if p.lower().endswith(".docx"):
                    rep = analyze_docx(p)
                    self.docx_path = p
                    self.docx_text = "\n".join(rep.texts)
                    self.txt.delete("1.0", "end")
                    self.txt.insert("1.0", self.docx_text)
                    self.show_report(rep)
                elif p.lower().endswith(".doc"):
                    messagebox.showwarning("صيغة قديمة", "صيغة .doc القديمة غير مدعومة. احفظ الملف بصيغة .docx من Word ثم أعد المحاولة.")
                else:
                    self.docx_path = self.docx_text = None
                    self.txt.delete("1.0", "end")
                    self.txt.insert("1.0", _read_txt(p))
                    self.analyze()
            except zipfile.BadZipFile:
                messagebox.showerror("خطأ", "الملف ليس .docx صالحًا.")
            except Exception as e:
                messagebox.showerror("خطأ", "تعذّر فتح الملف:\n%s" % e)

        def analyze(self):
            s = self.txt.get("1.0", "end-1c")
            if not s.strip():
                messagebox.showinfo("تنبيه", "ألصق نصًا أو افتح ملفًا أولًا.")
                return
            self.set_status("جارٍ التحليل…")
            try:
                if self.docx_path and s == self.docx_text:
                    rep = analyze_docx(self.docx_path)
                else:
                    rep = analyze_text(s)
                self.show_report(rep)
            except Exception as e:
                traceback.print_exc()
                messagebox.showerror("خطأ", str(e))

        def show_report(self, rep: Report):
            self.rep = rep
            # تلوين
            for c in list(CAT_COLORS) + ["jump"]:
                self.txt.tag_remove(c, "1.0", "end")
            self.txt.tag_remove("rtl", "1.0", "end")
            allt = "".join(rep.texts)
            if allt and len(AR_RE.findall(allt)) > 0.3 * max(1, sum(c.isalpha() for c in allt)):
                self.txt.tag_add("rtl", "1.0", "end")
            for f in rep.findings:
                if f.para >= 0 and f.end > f.start and f.cat in CAT_COLORS:
                    self.txt.tag_add(f.cat, "%d.%d" % (f.para + 1, f.start), "%d.%d" % (f.para + 1, f.end))
            # الحكم
            self.lbl_verdict.config(text=rep.verdict, fg=rep.color)
            self.lbl_score.config(text="مؤشر الذكاء الاصطناعي: %.0f / 100   |   الثقة بالحكم: %.0f%%" % (rep.score, rep.confidence * 100))
            self.draw_bar(rep.score if rep.words >= 40 else None)
            self.lbl_stats.config(text="   |   ".join("%s: %s" % kv for kv in list(rep.stats.items())[:4]))
            self.tr_feat.delete(*self.tr_feat.get_children())
            for k, (v, w, c) in sorted(rep.features.items(), key=lambda kv: -abs(kv[1][2])):
                if v > 0.005:
                    self.tr_feat.insert("", "end", text=FEATURE_LABELS[k], values=("%.2f" % v, "%+.2f" % c),
                                        tags=("ai" if c > 0 else "hu",))
            self.fill_findings()
            # الأسباب وخريطة الفقرات
            self.txt_reasons.config(state="normal")
            self.txt_reasons.delete("1.0", "end")
            for ln in rep.reasons:
                tag = "ai" if ln.startswith("▲") or (ln.startswith("   ") and ln.strip().startswith("+")) else (
                    "hu" if ln.startswith("▼") or (ln.startswith("   ") and ln.strip().startswith("-")) else "")
                self.txt_reasons.insert("end", ln + "\n", (tag,) if tag else ())
            self.txt_reasons.tag_add("rtl", "1.0", "end")
            self.txt_reasons.config(state="disabled")
            self.tr_para.delete(*self.tr_para.get_children())
            self.para_pos = {}
            for (i, sc, n) in rep.para_scores:
                iid = self.tr_para.insert("", "end", values=(i + 1, "%.0f" % sc, n, rep.texts[i][:90]),
                                          tags=("hi" if sc >= 55 else ("mid" if sc >= 30 else ""),))
                self.para_pos[iid] = i
            # معلومات
            lines = ["الفئات المرصودة:"]
            for c, n in rep.count_by_cat().most_common():
                if c != "meta":
                    lines.append("   • %s: %d" % (CAT_LABELS[c], n))
            lines.append("")
            lines.append("إحصاءات النص:")
            lines += ["   • %s: %s" % kv for kv in rep.stats.items()]
            metas = [f.text for f in rep.findings if f.kind == "meta"]
            if rep.is_docx:
                lines.append("")
                lines.append("بيانات ملف Word:")
                for k, lab in (("creator", "المنشئ"), ("last_modified_by", "آخر تعديل بواسطة"), ("application", "التطبيق"),
                               ("created", "تاريخ الإنشاء"), ("modified", "تاريخ آخر تعديل")):
                    if rep.meta.get(k):
                        lines.append("   • %s: %s" % (lab, rep.meta[k]))
                lines += ["   • " + m for m in metas]
            self.info.config(state="normal")
            self.info.delete("1.0", "end")
            self.info.insert("1.0", "\n".join(lines))
            self.info.tag_add("rtl", "1.0", "end")
            self.info.config(state="disabled")
            self.set_status("اكتمل التحليل — %d موضعًا مرصودًا في %d كلمة." % (len([f for f in rep.findings if f.kind != "meta"]), rep.words))

        def draw_bar(self, score):
            cv = self.cv
            cv.delete("all")
            w = max(cv.winfo_width(), 380)
            h = 22
            zones = ((0, 25, "#8ce99a"), (25, 40, "#c0eb75"), (40, 55, "#ffe066"), (55, 70, "#ffa94d"), (70, 100, "#ff8787"))
            for a, b, col in zones:
                cv.create_rectangle(w * a / 100, 4, w * b / 100, 4 + h, fill=col, outline="")
            cv.create_text(4, 4 + h + 7, text="بشري", anchor="w", font=("Tahoma", 8))
            cv.create_text(w - 4, 4 + h + 7, text="ذكاء اصطناعي", anchor="e", font=("Tahoma", 8))
            if score is not None:
                x = w * score / 100
                cv.create_polygon(x - 7, 0, x + 7, 0, x, 12, fill="#212529")
                cv.create_line(x, 0, x, 4 + h, width=2, fill="#212529")

        def fill_findings(self):
            self.tr.delete(*self.tr.get_children())
            self.row_pos = {}
            if not self.rep:
                return
            sel = self.cmb.get()
            for f in self.rep.findings:
                if f.kind == "meta":
                    continue
                if sel != "الكل" and CAT_LABELS[f.cat] != sel:
                    continue
                iid = self.tr.insert("", "end", values=(f.label, f.para + 1 if f.para >= 0 else "-", f.text, f.note))
                self.row_pos[iid] = (f.para, f.start, f.end)

        def on_para_select(self, _e):
            sel = self.tr_para.selection()
            if not sel:
                return
            para = self.para_pos.get(sel[0], -1)
            if para < 0:
                return
            self.txt.tag_remove("jump", "1.0", "end")
            a = "%d.0" % (para + 1)
            self.txt.tag_add("jump", a, "%d.end" % (para + 1))
            self.txt.see(a)

        def on_select(self, _e):
            sel = self.tr.selection()
            if not sel:
                return
            para, s, e = self.row_pos.get(sel[0], (-1, 0, 0))
            if para < 0:
                return
            self.txt.tag_remove("jump", "1.0", "end")
            a, b = "%d.%d" % (para + 1, s), "%d.%d" % (para + 1, max(e, s + 1))
            self.txt.tag_add("jump", a, b)
            self.txt.see(a)

        def export_html(self):
            if not self.rep:
                messagebox.showinfo("تنبيه", "حلّل نصًا أولًا.")
                return
            p = filedialog.asksaveasfilename(defaultextension=".html", filetypes=[("HTML", "*.html")],
                                             initialfile="تقرير_الفحص.html")
            if p:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(report_to_html(self.rep))
                self.set_status("حُفظ التقرير: " + p)
                try:
                    os.startfile(p)  # type: ignore[attr-defined]
                except Exception:
                    pass

        # ── دفعات ──
        def batch_folder(self):
            d = filedialog.askdirectory(title="اختر مجلدًا يحوي ملفات docx / txt")
            if not d:
                return
            paths = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith((".docx", ".txt", ".md")) and not f.startswith("~$")]
            self.run_batch(paths)

        def run_batch(self, paths):
            if not paths:
                messagebox.showinfo("تنبيه", "لا ملفات مناسبة.")
                return
            import threading
            results, state = [], {"done": False}

            def work():
                for p in paths:
                    try:
                        r = analyze_file(p)
                        if r:
                            results.append((os.path.basename(p), r))
                    except Exception as e:
                        results.append((os.path.basename(p), e))
                state["done"] = True

            threading.Thread(target=work, daemon=True).start()

            def poll():
                self.set_status("جارٍ تحليل الملفات… %d / %d" % (len(results), len(paths)))
                if state["done"]:
                    self.show_batch(results)
                else:
                    self.after(200, poll)
            poll()

        def show_batch(self, results):
            win = tk.Toplevel(self)
            win.title("نتائج تحليل الملفات")
            win.geometry("900x480")
            cols = ("file", "words", "score", "verdict", "err", "fmt", "meta")
            tv = ttk.Treeview(win, columns=cols, show="headings")
            for c, t, w in (("file", "الملف", 260), ("words", "الكلمات", 70), ("score", "المؤشر", 70),
                            ("verdict", "الحكم", 200), ("err", "أخطاء/1000", 80), ("fmt", "عدم اتساق التنسيق", 100), ("meta", "آثار تحرير", 80)):
                tv.heading(c, text=t)
                tv.column(c, width=w, anchor="center")
            rows = []
            for name, r in results:
                if isinstance(r, Exception):
                    row = (name, "-", "-", "خطأ: %s" % r, "-", "-", "-")
                else:
                    row = (name, r.words, "%.0f" % r.score, r.verdict, r.stats["أخطاء مرجّحة لكل 1000 كلمة"],
                           "%.2f" % r.fvals["fmt_incons"], "%.2f" % r.fvals["meta_human"])
                rows.append(row)
                tv.insert("", "end", values=row)
            tv.pack(fill="both", expand=True, padx=6, pady=6)

            def save_csv():
                p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="نتائج_الفحص.csv")
                if p:
                    with open(p, "w", encoding="utf-8-sig", newline="") as f:
                        wr = csv.writer(f)
                        wr.writerow(["file", "words", "ai_score", "verdict", "errors_per_1000", "format_inconsistency", "human_edit_traces"])
                        wr.writerows(rows)
            ttk.Button(win, text="💾 حفظ كـ CSV", command=save_csv).pack(pady=6)
            self.set_status("اكتمل تحليل %d ملفًا." % len(results))

    App().mainloop()


# ════════════════════════════════════════════════════════════════════════════
def main():
    args = sys.argv[1:]
    if args and args[0] == "--calibrate" and len(args) == 3:
        calibrate(args[1], args[2])
        return
    if args and args[0] == "--cli":
        for p in args[1:]:
            r = analyze_file(p)
            if not r:
                print(p, ": صيغة غير مدعومة")
                continue
            print("=" * 70)
            print(p)
            print("المؤشر: %.0f/100  | %s | الثقة %.0f%% | كلمات %d" % (r.score, r.verdict, r.confidence * 100, r.words))
            for k, (v, w, c) in sorted(r.features.items(), key=lambda kv: -abs(kv[1][2])):
                if v > 0.005:
                    print("   %-45s قيمة %.2f  أثر %+.2f" % (FEATURE_LABELS[k], v, c))
            for ln in r.reasons:
                print("   " + ln)
            for kind, n in r.count_by_kind().most_common():
                if kind != "meta":
                    print("   [%s] %s: %d" % (KINDS[kind][1], KINDS[kind][0], n))
        return
    if args and args[0] == "--html" and len(args) == 3:
        r = analyze_file(args[1])
        if not r:
            print("صيغة غير مدعومة")
            return
        with open(args[2], "w", encoding="utf-8") as f:
            f.write(report_to_html(r))
        print("تم حفظ التقرير:", args[2])
        return
    try:
        run_gui()
    except Exception:
        err = traceback.format_exc()
        try:
            with open(os.path.join(HERE, "error.log"), "w", encoding="utf-8") as f:
                f.write(err)
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("خطأ", "حدث خطأ. التفاصيل في error.log\n\n" + err[-600:])
        except Exception:
            pass


if __name__ == "__main__":
    main()
