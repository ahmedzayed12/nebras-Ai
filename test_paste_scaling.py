# -*- coding: utf-8 -*-
"""Paste-layer traces count in proportion to what the wording says (formatting stays in the file after the words are rewritten)."""
import backend_adapter as b
from tests.test_detection_layers import LLM_PARA, HUMAN_PARA, _docx

WEB = '<w:pStyle w:val="NormalWeb"/><w:spacing w:beforeAutospacing="1" w:afterAutospacing="1"/>'
CHAT = '<w:rFonts w:ascii="Söhne" w:hAnsi="Söhne"/><w:color w:val="0D0D0D"/>'


def _human_paras(n=40):
    return ["Plate %d was read on day %d after sowing, and %s" % (i, 3 + i % 5, HUMAN_PARA) for i in range(n)]


def test_web_formatting_alone_cannot_lift_human_wording():
    plain = b.analyze_uploaded_bytes("a.docx", _docx(_human_paras()))
    webby = b.analyze_uploaded_bytes("b.docx", _docx(_human_paras(), ppr=WEB))
    assert webby.paste.total >= b.PASTE_MIN          # the old rule would have fired on these traces alone
    assert webby.score <= plain.score + 3.0


def test_web_formatting_still_counts_when_the_wording_agrees():
    paras = [LLM_PARA] * 40
    plain = b.analyze_uploaded_bytes("a.docx", _docx(paras))
    webby = b.analyze_uploaded_bytes("b.docx", _docx(paras, ppr=WEB))
    assert webby.score >= plain.score


def test_direct_chat_fingerprint_keeps_at_least_half_its_weight():
    plain = b.analyze_uploaded_bytes("a.docx", _docx(_human_paras()))
    chat = b.analyze_uploaded_bytes("b.docx", _docx(_human_paras(), run_props=CHAT))
    assert chat.score > plain.score + 3.0


def test_unconverged_wording_share_is_zero():
    r = b.analyze_uploaded_bytes("a.docx", _docx(_human_paras()))
    assert b.wording_share(r) == 0.0
