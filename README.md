# Nebras Ai detector

واجهة Streamlit عربية تعرض مؤشر قرائن أسلوبية مع خريطة فقرات وتقرير PDF.
The published English-prose scorer is now **Nebras balanced v3.0** (`balanced_engine.py`), with the unchanged v2.1 engine retained as a baseline and fallback.

## المسار الحالي v3.0

- يجمع قراءة عامة للنص وقراءة فقرات مستقلة؛ لا تستبدل إحداهما الأخرى.
- يفصل تاريخ Word والخطوط وآثار اللصق عن جهة تأليف الكلمات، في الاتجاهين.
- الأخطاء النصية المؤثرة تُحتسب بحدودها، ولا توجد قاعدة تخفيض تلقائي لمجرد استخدام أداة إعادة صياغة.
- اختلاف المسارات يعطي حكمًا غير حاسم. انخفاض المؤشر **لا يثبت بشرية النص**.
- الدرجة المعتمدة `final_score` / `primary_score(report)`؛ المقارنة القديمة `score_engine`؛ الحقول القديمة `score` و`diagnostic_score` للتشخيص فقط.
- خريطة الفقرات والتظليل يستخدمان المسار الجديد، دون تظليل مقاطع إضافية لبلوغ النسبة.
- النصوص العربية والعروض والنثر القصير لها مسار رجوع معلن إلى المحرك القديم؛ لا ندّعي تحقق v3 عليها.
- الحروف المتشابهة من أبجديات أخرى وzero-width تبقى دون تنظيف، كما طُلب.

نتائج التحقق المحدود، والمصادر المثبتة قبل 2020، والفشل المعروف على إعادة صياغة عادية: [benchmarks/README.md](benchmarks/README.md). هذه ليست نسبة دقة عامة أو نموذج تأليف احتمالي مُعاير.

الأقسام التالية التي تحمل v2.x تشرح المسار التاريخي والتشخيصي؛ سياسة النتيجة الحالية هي v3 أعلاه.

> المؤشر تقدير احتمالي مبني على قرائن أسلوبية، وليس إثباتًا لمن كتب النص. لا تعتمد عليه وحده في اتهام أحد أو في قرار عالي الأثر.

## المزايا

- إدخال نص مباشر، أو ملف واحد (`DOCX / TXT / MD`)، أو دفعة ملفات / ZIP مع تصدير CSV.
- لوحة نتيجة ومؤشر دائري وخريطة فقرات.
- تقرير PDF بتظليل **الجملة أو الفقرة كاملةً** حيث دلّت القرائن، على شكل ملف Word الأصلي (يحتاج LibreOffice، وهو مُعدّ تلقائيًا على Streamlit Cloud عبر `packages.txt`).

## هيكل المشروع

| الملف | الوظيفة |
|---|---|
| `streamlit_app.py` | الواجهة (نقطة التشغيل) |
| `backend_adapter.py` | تجهيز الإدخال، تدقيق حساب الطبقات، وتوحيد النتيجة المعتمدة والتصدير |
| `balanced_engine.py` | محرك v3 المنشور: فصل آثار الملف، تجميع القراءة العامة والموضعية، وبيان عدم اليقين |
| `AI_Text_Inspector.pyw` | محرك التحليل الأصلي v2.1 **دون أي تعديل** |
| `nebras_report.py` | بناء تقرير PDF (تظليل DOCX + غلاف + ترويسة) |
| `ui.css` + `static/fonts/` | التصميم والخطوط (تراخيص الخطوط داخل المجلد) |
| `requirements.txt` | حزم Python |
| `packages.txt` | حزم النظام لـ Streamlit Cloud (LibreOffice + خطوط تدعم العربية) |
| `.streamlit/config.toml` | الثيم وإعدادات الخادم |
| `academic_signals.py` | طبقة النثر الأكاديمي الإنجليزي المولَّد (مفردات LLM، الثلاثيات، الكلمات الطويلة، غياب ضمير المتكلم) |
| `provenance.py` | تحديد موضع الفقرة المُدخَلة داخل ملف Word: بصمات النسخ من واجهة الدردشة (الخط، اللون، الخلفية، Autospacing) على مستوى **كل فقرة** من XML الملف، لا على مستوى المستند |
| `format_forensics.py` | أدلة التنسيق: آثار لصق من واجهات الدردشة (الخط، اللون، التظليل، Autospacing، مسافات ثابتة)، وجزر التنسيق |
| `slide_mode.py` | وضع العروض التقديمية في PDF: التعرّف على العرض (بيانات الملف + شكل الصفحات) وقياس انتظام القالب بين الشرائح |
| `tools/calibrate_slides.py` | فحص/معايرة وضع العروض على عروضك البشرية والمولَّدة |
| `segment_signals.py` | (v2.5) قراءة المستند فقرةً فقرة وتقدير حصة النص المولَّد في المستندات المختلطة |
| `tools/calibrate_segments.py` | إعادة قياس مرتكزات القراءة الفقرية على نصوصك البشرية |
| `tools/check_file.py` | فحص ملف من سطر الأوامر دون متصفح |
| `tools/calibrate_academic.py` | إعادة معايرة العتبات على نصوصك المصنّفة (بشري / مولَّد) |
| `tests/` | اختبارات تطابق النتائج، الترميزات، PDF، وطبقات الكشف الجديدة |

## طبقات الكشف بعد المحرك (v2.2)

المحرك الأصلي لم يُمسّ (البصمة كما هي). أُضيفت بعده ثلاث طبقات في `backend_adapter._enhance`:

1. **طبقة النثر الأكاديمي** (`academic_signals.py`): لا تعمل إلا إذا اجتمعت ثلاث بصمات مستقلة على الأقل، ولا تستطيع إلا رفع المؤشر. مفرداتها مأخوذة من دراسات منشورة عن «المفردات الزائدة» في الأبحاث بعد ChatGPT، وعتباتها مضبوطة على 352 نصًا بشريًا كُتبت قبل 2020 (عتبة الصفر = المئين 99 للبشر).
2. **آثار اللصق في Word** (`format_forensics.py`): خط واجهة الدردشة أو الويب (Söhne، ui-sans-serif، Segoe UI…)، ولون نص ChatGPT (#0D0D0D / #374151)، وتظليل الخلفية، و Autospacing القادم من صفحات الويب، وعدد ثابت من المسافات المكتوبة قبل الفقرات، والكتل المتصلة ذات التنسيق المختلف.
3. **حدّ آثار الوعاء:** آثار التحرير وعدم اتساق التنسيق وخلط الاقتباس تدل على أن إنسانًا تعامل مع *الملف*، لا على أنه كتب *النص*. لذلك يُحدّ أثرها المجتمع عند −1.0، لكن **فقط** عندما تعمل الطبقة 1 أو الطبقة 2، فلا يرتفع المؤشر لملف بشري بلا قرائن مستقلة.

4. **وضع العروض التقديمية** (`slide_mode.py`): إذا كان الـ PDF عرضًا (PowerPoint أو Keynote أو Google Slides أو Canva…، أو صفحات عرضية بنصوص قصيرة)، يُحلَّل شريحةً شريحة. لا يُحتسب غياب النقطة في آخر العناوين خطأً بشريًا، ويُقاس انتظام القالب بين الشرائح: عنوان صغير مرقّم، وسطر خلاصة «تسمية: جملة»، وعدد بطاقات ثابت، وأوصاف متقاربة الطول بجمل تامة، ومفردات مولَّدة. يرفع المؤشر فقط عند اجتماع 3 بصمات، وتظهر نسبة **الشرائح** المشتبه بها. ملفات PDF المنتَجة من Word لا تُعامل كعروض أبدًا.

تُعرض أيضًا **نسبة النص المشتبه به** (حصة الكلمات في فقرات درجتها 55 فأكثر)، وهي المقابلة لنسبة Turnitin/GPTZero. أما المؤشر العام الحالي فهو مؤشر قرائن أسلوبية، وليس احتمال تأليف مُعايرًا.

## إصلاحات «الأسباب لا الضغط» (v2.3)

المحرك الأصلي لم يُمسّ (البصمة كما هي). الإصلاحات منطقية لا رقمية، ولكل منها اختبار يثبت أنه لا يعمي الكاشف عن النص المولَّد (`tests/test_human_fairness.py`):

1. **كلمات الموضوع ليست عادة أسلوبية:** الكلمات الموجودة في العناوين (مثل framework في أطروحة عن إطار تقييم) لا تُحتسب «مفردات مولَّدة».
2. **عناوين «عنوان: عنوان فرعي» ليست بنودًا مولَّدة:** تُستبعد العناوين من طبقة الأوراق التقنية.
3. **تخفيف آثار الملف البشرية يتناسب مع تقارب القرائن:** ثلاث بصمات = التخفيف الكامل كما كان؛ بصمتان = جزء منه فقط (`AcadLayer.strength`).
4. **خريطة الفقرات تأخذ أدلة الملف:** الدليل البشري على الملف يُطرح من لوغاريتم أرجحية كل فقرة (للأسفل فقط)، فلا يظهر «76% مشتبه» بجوار مؤشر منخفض.

للتشخيص: `python tools/diagnose_file.py الملف.docx` يعرض مصدر الرقم مرحلةً مرحلة.

### سياسة ملفات PDF (v2.4)

ملف PDF يحمل النص النهائي فقط: لا سجل تحرير ولا تنسيق يدوي ولا هفوات كتابة، وهي أدلة تحمي الكاتب البشري في Word. لذلك:

- حكم الـ PDF يُكتب «تقدير من النص وحده» ويوصي برفع ملف Word، ولا يستخدم الصيغتين القاطعتين («مولَّد على الأرجح» / «يميل بقوة») أبدًا. الرقم نفسه لا يتغير.
- بوابة جودة المراجع: إذا قُطِّعت قائمة المراجع في الاستخراج (مداخل لا تبدأ باسم أو بلا سنة) تُعطَّل قرينة «مراجع بلا إحالات» بدل أن تُحتسب. المداخل الكاملة غير المذكورة في المتن تبقى قرينة كما كانت.
- لفحص اتساق المسارات الثلاثة (Word / نص / PDF) على الملف نفسه: `python tools/route_consistency.py الملف.docx` (فجوة أكبر من 10 تعني ميزة حساسة للاستخراج).

### اتساق الواجهة (v2.5): الحكم والمؤشر والخريطة تصف المستند نفسه

خريطة الفقرات تقول *أين* الاشتباه، والمؤشر يقول *كم*. المتوسط المرجَّح بالكلمات لدرجات الفقرات هو نسبة النص المشتبه به المتوقعة، فلا يجوز أن يعلو على مؤشر المستند. عند التعارض (مثل «يميل إلى الكتابة البشرية» بجانب «70% فقرات مشتبه بها») تُنزَل كل الفقرات بالمقدار نفسه في لوغاريتم الأرجحية حتى يتساوى المتوسط مع المؤشر: ترتيب الفقرات لا يتغير، والفقرات القوية تبقى مشتبهة، والخريطة لا تُرفع أبدًا (`_anchor_map_to_index`). الاختبارات: `tests/test_map_consistency.py`.

> **مهم:** لا تعدّل `AI_Text_Inspector.pyw`. أي تغيير في بايت واحد يغيّر البصمة، فيتوقف التطبيق عمدًا لحماية تطابق النتائج
> (البصمة: `3692e8f4f4485460e1eb3f448a9f09f58c50685ceeb8ce49bb8921fad24a57f8`).

## قراءة المستند فقرةً فقرة (v2.5)

**المشكلة التي يعالجها:** المحرك والطبقات السابقة تحكم على الملف كتلةً واحدة. في رسالة كتب الطالب بعض فصولها بنفسه ولصق فصولًا أخرى من نافذة دردشة، تذوب عادات النص المولَّد في الفصول البشرية، ثم تسحب آثارُ عمل الإنسان على *الملف* (زمن التحرير، المراجعات، التنسيق اليدوي، خلط الاقتباس) المستندَ كلَّه إلى «بشري». وكان الإنقاذ معلّقًا على اجتماع 3 بصمات أكاديمية بالضبط، فإن نقصت بصمة هبط المؤشر من نحو 69 إلى نحو 12.

**ما تفعله الطبقة (`segment_signals.py`):**

1. لكل فقرة (أو لمجموعة بنود قائمة تُقرأ معًا) قرينة خاصة بها من عادات الصياغة المولَّدة. كل عادة مرتكزة على الكتابة البشرية: لا تُحتسب تحت المئين 75 للبشر، ولا تبلغ قوتها الكاملة إلا عند المئين 97. لذلك لا تحسم عادة واحدة الحكم وحدها.
2. القرينة التي تخص فقرة تبقى عليها:
   - الهفوة البشرية المكتوبة داخل الفقرة (خطأ إملائي، مسافة قبل الفاصلة، فاصلة ملتصقة، كلمة مكررة) تُحتسب بشرية لتلك الفقرة.
   - أثر نافذة الدردشة في تنسيق الفقرة نفسها (لون نص ChatGPT، خط الواجهة، Autospacing) يُحتسب مولَّدًا لتلك الفقرة.
   - أما آثار الملف (زمن التحرير والمراجعات وجلسات rsid وخلط الاقتباس) فلا تُفرض على الفقرات، لأنها تثبت أن إنسانًا عمل على الملف، لا من كتب كل فقرة.
3. النص المولَّد يأتي في كتل متصلة، لذا تُقرأ الفقرات تسلسلًا بنموذج حالتين (بشري/مولَّد) نادر التبدّل بين الجارتين. الفقرة البشرية المصقولة وحدها داخل نص بشري تبقى بشرية.
4. «الإنسان يخطئ»: إذا كان النص المشتبه به طويلًا وخاليًا من الهفوات، تتأكد فقراته المشتبه بها. النظافة لا تنشئ شبهة، بل تقوّي شبهة قائمة.
5. **المؤشر العام = حصة كلمات المتن الواقعة في الفقرات التي ينسبها المحرك إلى التوليد** (احتمالها 0.5 فأكثر)، وهو الرقم الذي يعرضه Turnitin وGPTZero. تُظلَّل في تقرير PDF هذه الفقرات نفسها كاملةً، فيساوي التظليل المؤشر دائمًا، ولا يوجد تظليل تكميلي.
6. إذا كانت قراءة المستند كلّه في المراحل السابقة فوق 80، وهو أعلى من كل ما قيس على الملفات البشرية، ترتفع قرينة كل فقرة لم تحسمها قرائنها بالتناسب. الفقرة التي قرائنها بشرية واضحة تبقى بشرية.
7. خطوات v2.3 (تجميع الفقرات، قاعدة الهفوات، أثر Word على الخريطة) ما زالت تعمل وتبقى نتائجها في التقرير للتشخيص (`score_pooled`، `file_shifts`)، لكن المؤشر النهائي يأتي من القراءة الفقرية.

المستندات العربية (أقل من 60% من المتن بالإنجليزية) تبقى على القراءة السابقة دون تغيير، لأن عادات الطبقة عادات إنجليزية.

**القياس:** منصة اختبار من ملفات Word مولَّدة برمجيًا، دون أي ملف من ملفات المستخدمين:
- نصوص بشرية منشورة قبل 2020: فصول كتب Language Science Press، وPython PEPs، وRust RFCs، وبعضها بهفوات كتابة واقعية.
- نصوص مولَّدة أكاديمية.
- ملفات مختلطة بحصص معروفة 30/50/70%، في حاويتين: ملف «عُمل عليه طويلًا» وملف «نظيف».
- مجموعة تطوير، ومجموعة اختبار منفصلة لم تُستخدم في الضبط.

| (مجموعة الاختبار المنفصلة) | قبل | v2.5 |
|---|---|---|
| بشري: متوسط المؤشر / أعلى قيمة | 17.7 / 49.6 | 1.7 / 37.2 |
| مختلط 30% (متوسط المؤشر) | 24.8 | 24.8 |
| مختلط 50% | 34.7 | 38.9 |
| مختلط 70% | 45.0 | 55.2 |
| الكلمات المولَّدة المكتشفة في خريطة الفقرات | 52% | 78% |
| الكلمات البشرية المتهمة خطأً | 8.4% | 2.6% |
| متوسط الخطأ المطلق في المختلط | 16 نقطة | 13 نقطة |

**v2.6 — الاستجابة للتعديل:** أُلغي رفع قرائن الفقرات بسبب قراءة المستند كله (كان يثبّت المؤشر عند 100 ولا يتأثر بإعادة الكتابة). وآثار أدوات الأنسنة وإعادة الصياغة (المسافات غير القياسية والعبارات المعذَّبة) تُرصد وتُذكر في التقرير معلومةً فقط ولا ترفع النسبة: استعمال الأداة يخفّض النسبة بقدر ما غيّر من الصياغة، كما في البرامج الأخرى. قاعدة «الإنسان يخطئ» باقية.

**مسافات أدوات «الأنسنة» (v2.5.2):** مسافة من نوع غير عادي (مثل U+2002) مزروعة بين كلمتين إنجليزيتين. لوحة المفاتيح لا تكتبها، وقيس ذلك على 2254 ملفًا بشريًا فلم تظهر واحدة منها. إذا وُجد منها 10 فأكثر في المستند، تُحتسب في كل فقرة تحملها **قرينة متوسطة واحدة** (+1.0 لوغاريتم أرجحية) تُضاف إلى قرائن صياغتها، ولا تحسم الحكم وحدها. لذلك تنخفض نسبة النص المعالَج بأداة الأنسنة عن نسبته الأصلية، كما في البرامج التجارية، دون أن تهبط إلى الصفر. أما الحروف غير المرئية داخل الكلمات والحروف السيريلية الشبيهة باللاتينية فلا تُحتسب ولا يُنظَّف النص منها، لأنها تُستعمل أيضًا لإخفاء النسخ عن أجهزة كشف الاقتباس، فلا تدل على من كتب. يُقرأ الملف كما هو.

**عبارات أدوات إعادة الصياغة (v2.5.5):** مصطلح علمي ثابت استبدلته أداة إعادة صياغة آلية بمرادفات حرفية، مثل «counterfeit consciousness» بدل artificial intelligence، و«irregular woodland» بدل random forest. وثّقها Cabanac وزملاؤه (2021). لم تظهر واحدة منها في 2254 ملفًا بشريًا، ولذلك تكفي عبارة واحدة لتكون قرينة على فقرتها، بالوزن المخفَّض نفسه (+1.0).

**حق الفقرة في قرائنها:** الجيران يحسمون الفقرة التي لم تحسمها قرائنها فقط؛ الفقرة التي تميل قرائنها هي إلى البشري (−1.0 فأقل) لا تُنسب إلى التوليد بسبب ما حولها.

**حدود معروفة:** النص المولَّد المكتوب بصوت المتكلم («I noticed…») والنص المولَّد الحديث الخالي من العبارات النمطية يمرّان غالبًا كبشريين، فالطبقة تختار الخطأ في اتجاه حماية الكاتب البشري. وتقدير الحصة في المختلط أقل من الحقيقة بنحو 5–14 نقطة في المتوسط.

## التشغيل محليًا (أسهل طريقة)

- **Windows:** انقر نقرًا مزدوجًا على `run_local_windows.bat`. يُنشئ البيئة `.venv` ويثبّت الحزم في المرة الأولى، ثم يشغّل الاختبارات ويفتح التطبيق على `http://localhost:8501`.
- **macOS / Linux:** `bash run_local_mac_linux.sh`
- فحص ملف دون متصفح: `python tools\check_file.py "C:\path\file.docx"`

يحتاج Python 3.11 أو 3.12 (فعّل خيار *Add python.exe to PATH* عند التثبيت).

## التشغيل محليًا (يدويًا)

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      Linux/macOS:  source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

لإنتاج PDF محليًا ثبّت LibreOffice (أو Microsoft Word على Windows). الاختبارات:

```bash
pip install pytest
python -m pytest tests -q
```

### استجابة المؤشر لإعادة كتابة الفقرات

المؤشر النهائي يقيّد تجميع القرائن على مستوى المستند بمتوسط درجات الفقرات موزونًا بعدد كلماتها:
`final = min(pooled, sum(paragraph_score * words) / sum(words))`.
يمنع ذلك بقاء المستند المختلط قريبًا من 100 بسبب تشبع تجميع القرائن العامة رغم وجود فقرات منخفضة الاشتباه.
تحتفظ أداة `tools/diagnose_file.py` بالمؤشر المجمع ومراحل الحساب للتشخيص؛ لا تضيف تفاصيل إلى الواجهة أو PDF.
العروض التقديمية تحتفظ بمسار تحليل الشرائح. الدرجة استدلالية وليست احتمالًا مثبت المعايرة أو نسبة مؤكدة للكلمات المولدة.
اختبارات الاستجابة في `tests/test_revision_sensitivity.py` تستخدم عينات مستقلة عن ملفات المستخدم؛ يلزم تقييم على مجموعة موسومة مستقلة لإثبات دقة الكشف ومعدل الإنذارات الكاذبة.

### توطين القرائن وحماية النص البشري

- تُعاد درجات الفقرات من القرائن التي بقيت بعد استبعاد القوائم التقنية وكلمات الموضوع؛ لا تبقى العلامات المستبعدة مؤثرة في الخريطة.
- الترقيم والترتيب العددي وMarkdown وحدها لا تنشئ قرائن محلية على مصدر النص. كما لا تُعامل أسماء متغيرات البحث في القوائم الثلاثية كعادة بلاغية إذا سمّتها العناوين والكلمات المفتاحية.
- تعمل طبقة `ai_signals.py` المحلية للعربية والإنجليزية بمراجعة مشروطة: لا ترفع النتيجة إلا مع تقارب قرائن الصياغة، وتُستبعد كلمات الموضوع منها أيضًا. لا تستخدم نماذج أو خدمات كشف خارجية.
- يستهدف التظليل جملًا كاملة تحمل قرائن نصية محتسبة. لا يملأ الفجوات بين الجمل ولا يمتد إلى فقرات تشترك في `rsid` أو تنسيق اللصق فقط. تُظلّل الفقرة كاملة عندما تتصل جملها المدعومة بالقرائن.
- في PDF الأصلي تُرفض المطابقة النصية الضعيفة بدل تظليل كلمات متناثرة، وتُحسب حدود السطر العربي من طرفيه. لا يتجاوز التظليل ميزانيته بإضافة جزء من وحدة كاملة.

تغطي `tests/test_targeted_highlights.py` حماية النص العلمي المصقول، والعربية البشرية، والقياسات والجداول، وتوطين الجمل، وحدود تظليل RTL. هذه اختبارات سلوك وتراجع، وليست قياسًا مستقلًا للدقة التجارية. صفحات PDF المصورة التي لا تحمل طبقة نصية لا تحصل على تظليل موضعي تلقائي من هذه الآلية.

### Cloudflare Turnstile على Streamlit Community Cloud

يدعم التطبيق Turnstile بمكوّن Streamlit مخصص، والتحقق من الرمز على الخادم عبر Siteverify قبل فحص النص والملف والدفعة وإعادة الفحص. الرموز أحادية الاستخدام ومربوطة بتحدٍ خاص بالجلسة؛ لا يُعتمد على إشارة نجاح المتصفح وحدها. لا يعمل الفحص إذا غاب المفتاح السري أو تعذّر التحقق.

1. في Cloudflare → Turnstile أضف اسم مضيف التطبيق الفعلي، مثل `your-app.streamlit.app`، إلى Allowed hostnames. أضف النطاق المخصص أيضًا إذا استُخدم. لا تضف مخطط `https://` أو مسارًا.
2. في Streamlit Cloud → Settings → Secrets أدخل المفتاح السري المرتبط بمفتاح الموقع بصيغة TOML، ثم احفظ:

```toml
TURNSTILE_SECRET_KEY = "YOUR_CLOUDFLARE_SECRET_KEY"
# اختياري: قيد إضافي يراجعه الخادم؛ استبدله باسم التطبيق الفعلي.
# TURNSTILE_HOSTNAMES = ["your-app.streamlit.app"]
```

مفتاح الموقع المعلن `0x4AAAAAAFUeD40X5Nr1Y-ox` محفوظ في الكود. يمكن تغييره باستخدام `TURNSTILE_SITE_KEY` في Secrets أو متغيرات البيئة. لا تضع المفتاح السري في GitHub أو في HTML. يدعم التطوير المحلي ملف `.streamlit/secrets.toml` المستبعد من Git أو متغيرات البيئة بالأسماء نفسها.

3. تأكد من أن التطبيق مرتبط بفرع `main` ثم أعد تشغيله، وأكمل تحدي Turnstile قبل الضغط على الفحص. التحقق يحتاج اتصال الخادم والمتصفح بـ `challenges.cloudflare.com`.

وثائق المزود: [التحقق على الخادم](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/)، [مفاتيح الاختبار](https://developers.cloudflare.com/turnstile/troubleshooting/testing/). اختبارات المشروع تحاكي استجابات التحقق وتثبت الحماية ومسار التطبيق؛ يلزم المفتاح السري وإعداد النطاق لإجراء اختبار فعلي على التطبيق المنشور.

## الرفع على GitHub

```bash
git init
git add .
git commit -m "Nebras Ai detector"
git branch -M main
git remote add origin https://github.com/<USERNAME>/<REPO>.git
git push -u origin main
```

## النشر على Streamlit Community Cloud

1. ادخل إلى [share.streamlit.io](https://share.streamlit.io) بحساب GitHub، واضغط **Create app**.
2. اختر المستودع والفرع `main`، وفي **Main file path** اكتب `streamlit_app.py`.
3. من **Advanced settings** اختر **Python 3.12**.
4. اضغط **Deploy**. أول نشر يستغرق عدة دقائق لأن `packages.txt` يثبّت LibreOffice.

### ملاحظات

- **الخصوصية:** المستندات المرفوعة تُعالج على خوادم Streamlit. لا ترفع ملفات حساسة. إن لزم، اجعل المستودع والتطبيق خاصين.
- **الموارد:** الخطة المجانية ذات ذاكرة محدودة؛ الملفات الكبيرة والدفعات الضخمة مع تحويل PDF قد تبطئ التطبيق أو تُعيد تشغيله.
- **السكون:** التطبيق ينام عند عدم الاستخدام لفترة، ويستيقظ بزر واحد.
- **PDF للنص الملصق وملفات TXT:** يُبنى مستند Word بسيط داخليًا ثم يُحوَّل، فيكون شكل الصفحات موحدًا وليس نسخة من ملف أصلي.
- عند تغيّر خطوط الخادم عن جهازك قد تختلف ترقيم صفحات PDF قليلًا عن عرض Word.

## التوثيق الإضافي

- `docs/CHANGELOG.txt` — سجل التعديلات على المؤشر وتقرير PDF.
- `docs/ENGINE_README_AR.txt` — شرح المحرك الأصلي وحدوده.

## ترخيص الخطوط

IBM Plex Sans Arabic وReem Kufi تحت ترخيص SIL OFL، ونصوص الترخيص داخل `static/fonts/`. أضف ملف `LICENSE` لمشروعك بحسب اختيارك قبل النشر العام.


## تحديد موضع الفقرة في تقرير PDF (v2.5)

المحرك الأصلي لم يُمسّ. تغيّر ما يُظلَّل في التقرير فقط:

1. **لا تعبئة لبلوغ المؤشر.** كانت الخطوة القديمة تزيد التظليل «التوضيحي» حتى تتساوى نسبته مع مؤشر المحرك، فكانت تُظلَّل فقرات بشرية. الآن لا يُظلَّل إلا ما دلّت عليه القرائن، ولا يُقطع التظليل داخل جملة.
2. **أدلة الملف على مستوى الفقرة** (`provenance.py`): خط واجهة الدردشة ولونها وخلفيتها وAutospacing تُقرأ من XML كل فقرة، وتُضاف إلى لوغاريتم أرجحية الفقرة **للأعلى فقط**، ولا تعمل إلا إذا غطّت الأثرة نصف حروف الفقرة على الأقل، وإلا إذا كان أسلوب الفقرة غير بشري بوضوح (`MIN_WORDING`). فقرة بشرية لُصقت من ويب بخط مشابه لا تُظلَّل.
3. **الكتلة الملصوقة كوحدة:** الفقرات المتجاورة بالبصمة نفسها كتلة واحدة؛ إن ثبت أحد أعضائها صار الباقي «محتملًا» (سماوي باهت)، وتنضم الفقرة القصيرة غير المقيَّمة داخل الكتلة، وتمتد حافتا الكتلة بالتظليل الباهت إلى الفقرة المجاورة المكتوبة في جلسة التحرير نفسها (`rsid`).
4. **الوحدة فقرة كاملة أو جملة كاملة:** فقرة وُسمت بوصفها وحدة تُظلَّل من أول جملة إلى آخرها؛ وفي غيرها تُسدّ فجوة جملة واحدة بين جملتين مظلّلتين، وتُظلَّل الفقرة كلها إذا كان ≥ 60% من كلماتها مظلّلًا ولا أثر بشريًا فيها.
5. **سقف متسق مع المؤشر:** لا يتجاوز التظليل نسبة المؤشر بأكثر من 10 نقاط (`INDEX_SLACK`)؛ وإن تجاوزها تُسقَط أضعف الوحدات ثقةً كاملةً.

الاختبارات: `tests/test_provenance.py`.

## تصحيح عدّ الأدلة الداخلية (v2.6)

مصدر الخطأ الذي وُجد: كلمات موضوع البحث وقوائمه التقنية كانت تُحسب علامات أسلوب. التصحيحات عامة ومنطقية:

1. **العدّ بالتنوع:** الكلمة الواحدة تُحتسب حتى 3 مرات في معجم الطبقتين (`STEM_CAP`)، فتوقيع النماذج اللغوية يظهر في كلمات كثيرة مختلفة، أما كلمة تتكرر عشرات المرات (framework، robustness) فهي موضوع النص.
2. **كلمات الموضوع** تُستخرج أيضًا من سطر الكلمات المفتاحية، لا من العناوين المنسّقة بنمط Word وحدها.
3. **القوائم التقنية ليست ثلاثيات بلاغية:** «MQTT, CoAP, and Zigbee» و«128, 64, and 32» لا تُحتسب (`academic_signals.technical_list`)، ولا تُحتسب دليل تظليل.
4. **الطبقة التقنية:** لا ترفع المؤشر ببصمة واحدة، وتُستثنى التسميات والجداول والكلمات المفتاحية والمقدمات من النثر الذي تقيسه.
5. **أخطاء المراجع والجداول** (مراجع بلا إحالة، تسمية بلا إشارة) لا تُحتسب مؤيدة للذكاء الاصطناعي (`backend_adapter._structural_neutral`).
6. **فقرات نثرية بنمط عنوان:** فقرة من 25 كلمة فصاعدًا تنتهي بعلامة جملة تُحلَّل نثرًا ولو حملت نمط Heading في الملف (`docx_clean._restore_prose_headings`)؛ كانت تُستبعد من التحليل.

## بصمة المولّد والرفع من الهاتف (v2.7)

1. **بصمة المولّد الصريحة** (منشئ الملف `python-docx` أو أداة معروفة، أو تاريخ قالبها 2013-12-23): كانت محدودة بـ +0.1 دائمًا. الآن تُحتسب **نصفها** لأنها شهادة مباشرة من الملف بأن نصّه أُدخل ببرنامج لا بالكتابة في Word، و**الباقي بقدر اتفاق طبقة النثر الأكاديمي المستقلة** (المعايَرة على نصوص خارجية لا على بيانات الملف) معها. بصمات التحرير الضعيفة بلا اسم مولّد (جلسة واحدة، زمن تحرير 0) تبقى محدودة كما كانت؛ و`pandoc` ليس مولّدًا صريحًا. المقدار الذي احتُسب فعلًا يظهر في «أسباب التقدير»، ويرتفع به مستوى الفقرات كلها معًا لأن الأثر عن الملف كله (`backend_adapter._generator_to_paragraphs`).
2. **الإحصاء والصيغ ليسا هفوات كتابة:** الكسور بلا صفر بادئ (`p < .001`, `r = .34`) وقوائم الأدلة المنخفضة (`{h,c}`) كانت تُقرأ «مسافة قبل علامة ترقيم» أي دليلًا بشريًا؛ تُستبعد الآن وتُعاد كثافة الأخطاء (`_notation_neutral`).
3. **الرفع من أندرويد:** يُعرَّف نوع الملف من بايتاته (PDF / DOCX / ZIP / DOC / نص) مهما سمّاه الهاتف (`sniff_extension`)، فلا يُرفض ملف بلا امتداد أو باسم `report.docx.bin`. وفي `.streamlit/config.toml` أُوقف فحص XSRF/CORS وضغط WebSocket؛ فهذه أشهر أسباب «فشل الرفع» في متصفحات الهاتف.
4. سطر في أسباب التقدير يوضح أن PDF يحمل النص وحده.

الاختبارات: `tests/test_generator_and_uploads.py`.

## بنية الفصول لا تُحتسب دليلًا (v2.8)

أخطاء وُجدت بفحص ملف فصل رسالة، وكلها كانت تُحتسب **دليلًا بشريًا كاذبًا** أو **دليلًا آليًا كاذبًا**:

1. **التسميات المرقّمة بالفصل** `Table (3-1).` / `Figure (3-2).` لم يتعرف عليها المحرك ولا `docx_clean`، فكان حجم خطها ومحاذاتها وتباعدها يُعدّ «تنسيقًا يدويًا داخل المتن» (−1.5 في ملف واحد). صارت تُعامل كعناصر هيكلية بتنسيق مقصود (`docx_clean._STRUCTURAL`).
2. **المعادلات المكتوبة نصًّا** وفي آخرها رقمها `... × 100 (3-1)` تُستبعد من النثر (`_EQ_LINE`).
3. **أسطر العنوان قبل أول فقرة والفقرات الفارغة فوقها** (صفحة الغلاف) تُعد مقدمة لا نثرًا؛ لا تُحتسب «فقرات فارغة للتباعد» (`_front_matter_ids`).
4. **اختصارات الأنواع والمراجع** `spp.` `sp.` `var.` `et al.` `Fig.` لا تُعدّ نهاية جملة تليها كلمة بحرف صغير (`_notation_neutral`).
5. **في الاتجاه المعاكس:** قوائم الأسماء (أنواع فطريات، أماكن، اختصارات، أرقام) ليست ثلاثيات بلاغية؛ تُستبعد جملها من عدّ `tricolon` ويُعاد اشتقاق قيمته، ويُحدَّث سطر السبب بالمقدار الجديد (`_lists_neutral`، `academic_signals.technical_list`).
6. أسطر «أسباب التقدير» للأخطاء والثلاثيات وبصمة المولّد تُعرض بمقدارها الفعلي بعد التصحيح فلا تناقض بينها وبين المؤشر.

الاختبارات: `tests/test_structure_fairness.py`.

## v2.9
- **Balanced-triad rule (academic_signals.balanced_triad / rhetorical_triad)**: a match for "A, B and C" counts as a rhetorical triad only when it is short and parallel and carries no clause word or inner and/or; the loose regex's lead-in words are ignored and the "no serial comma" form is handled. LLM-style triads ("institutional, organizational, and cultural") still count.
- **Photographs in the body (`photo_count`, `_photo_evidence`)**: distinct JPEG photographs (>= 20 KB) that the body actually references give a small human-side evidence of -0.1 log-odds each, capped at -0.4. It shows someone produced first-hand material; it does not prove who typed the sentences. No photos = no effect; withheld when the file names a generator. Fixed constants, not fitted to any file.
- Tests added for both.


### v2.7
- مسافات أدوات «الأنسنة» (مثل U+2002 بين الكلمات الإنجليزية) تُنظَّف إلى مسافات عادية، وتُحتسب قرينةً متوسطة في الفقرات التي حملتها (SPACE_L = 1.0). الحروف المقنّعة (Cyrillic / zero-width) تبقى دون تنظيف كما كانت.
- v2.7.1: الترميز العلمي (cobalt(II)، N,S-donor، 2:1، [CoL2(NO3)2]) لا يُحتسب «خطأ كتابة بشريًا»؛ أما Ulysses(1922) فيبقى خطأً حقيقيًا.
- v2.8: حاجز الدليل الذاتي يُحكم على دليل الفقرة نفسها (صياغتها وأخطاؤها وآثار اللصق/الأدوات فيها) قبل أي دفع على مستوى الملف؛ فلا تُظلَّل فقرة صياغتها بشرية بوضوح بسبب الميتاداتا أو قاعدة «البشر يخطئون» أو جيرانها.
- v2.9: الفقرات التي وُضعت في ملف Word من خارجه (بلا علامات التحرير rsid التي يضعها Word على كل فقرة تُكتب أو تُلصق فيه، في ملف باقيه يحملها) تُحتسب قرينة أداة في فقراتها (وزن أثر اللصق نفسه)، ولا تُظلِّل فقرة صياغتها بشرية بوضوح. الملفات الخالية من العلامات كلها لا تتأثر.

# Formal-prose fairness correction

The academic layer does not count absence of first-person pronouns as another
independent habit. Vocabulary plus long words alone cannot activate its fusion.
Paragraph scoring pools overlapping vocabulary dictionaries and morphological
complexity instead of adding them as independent evidence, and uses the academic
triad filter to exclude technical enumerations. The pooled document score is not
fed back into paragraph evidence. A paragraph needs positive local evidence to
be attributed to generation, even inside a suspected block.

These are general evidence corrections, not calibration against a particular
user document or another detector's reported percentage. The headline is a continuous word-weighted style index, not a verified authorship
percentage. The thresholded share of flagged prose is retained separately.
Rewriting can reduce it when measured local evidence changes; paraphrasing or
adding errors must not guarantee a predetermined reduction. Deployment thresholds
still require held-out, provenance-labelled academic texts, including non-native
English, proofread human work, weak generation, and human/automatic paraphrases.

### Continuous index correction

The headline uses all paragraph evidence weighted by eligible word counts, rather
than counting only paragraphs over 0.5. Low nonzero evidence no longer disappears
into an exact zero, and crossing the highlighting threshold does not cause a
whole-paragraph jump. Highlighting still requires the local evidence threshold;
it is never expanded to match the headline. A local-evidence veto caps the
paragraph at its own evidence strength rather than flattening it to 49 percent.
Cosmetic spacing is excluded from human-error credit consistently across layers.
There is no artificial minimum score; a displayed 0.0 may still occur by rounding.

### Technical-channel transfer and stage audit

The generic paragraph segmenter now preserves locally corroborated technical
wording from the technical layer when that layer has document-level convergence.
Each supported paragraph uses the stronger wording channel, not their sum, before
the same bounded local error discount. A human paragraph cannot inherit support
merely from its technical neighbours. The original engine remains unchanged.

`tools/diagnose_file.py` reports the main wording-only score at each stage and
weighted wording log-odds, error discounts, and technical support in the final
segmenter. Low results on paraphrases are still possible: this rules engine does
not reconstruct text provenance after substantial rewriting. Passing regression
tests is not a measured sensitivity rate on humanized documents. Confusable
letters and zero-width masking remain untouched.

### Historical result policy: original engine (superseded by v3)

Before v3, the published result came from the original engine (`score_engine`) through
`primary_score(report)`. The single-file gauge, batch table, CSV and PDF all use
this selector. Uploaded/text analysis exposes `final_score`, `final_verdict`,
`final_color`, and `score_source="main_engine"`. Existing `report.score` remains
the later layers' diagnostic index for compatibility; `diagnostic_score` names
it explicitly. Consumers must use `final_score` or `primary_score`, not the
legacy diagnostic field, for the authoritative result.

Paragraph analysis and shading remain local diagnostic evidence. They neither
replace the main result nor need to cover the same percentage of the document.
Selecting the original engine is an output policy, not a claim that its
authorship accuracy has been validated on all rewritten texts. Confusable
letters are still left untouched.

### Arithmetic audit and evidence ledger

`python tools/audit_scoring.py file.docx [more.docx]` emits a read-only JSON
audit of the raw document engine result, prepared-input engine result, exact
reconstruction from bias and feature contributions, intermediate stages, and
published versus diagnostic results. Preparation can change the engine input;
these two original-engine runs must not be conflated. Input documents are not
rewritten and the audit performs no training or threshold tuning.

Container recalibration now updates feature contributions and its logit together,
and repeating the same recalibration is idempotent. Non-logistic technical,
academic and local style fusions are replayed after later feature corrections;
a fused percentage is never treated as the base engine's feature logit. Paste
contributions are recorded explicitly and excluded from wording-only evidence.
HTML export uses a snapshot of the original engine evidence with its headline,
instead of explaining that headline using the later feature table.

These arithmetic repairs do not change the authoritative main-engine selection
or establish authorship accuracy. Low direct-engine results cannot automatically
be blamed on the later layers. Regression tests check mathematical consistency,
not empirical sensitivity or a target percentage on submitted documents.
