# -*- coding: utf-8 -*-
"""Conversation tests Ask AI — pola CALM test stories, tanpa Odoo.

Menguji rantai multi-turn pada level sinyal/transisi murni
(flow_signal + flow_next + repair detection + command schema),
tanpa database dan tanpa server. Aturan tetap: korpus ukur, bukan latih —
skenario di sini TIDAK BOLEH disalin ke EXEMPLARS NLU.

Jalankan mandiri::

    python -m unittest addons.it_asset.tests.test_ask_ai_flows -v

(dari root repo; atau ``python tests/test_ask_ai_flows.py`` via __main__.)
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODELS = os.path.normpath(os.path.join(_HERE, "..", "models"))
if _MODELS not in sys.path:
    sys.path.insert(0, _MODELS)

import ask_ai_commands as cmds


def run_story(steps):
    """Jalankan satu story: [(flow, state, text, ref_hit, info)].

    Kembalikan list action transisi. Text dipetakan ke sinyal via
    cmds.flow_signal (cermin pemetaan di backend _consume_flow).
    """
    actions = []
    state = {"step": "await_choice", "slots": {}}
    flow = steps[0][0] if steps else ""
    for flow_name, st, text, ref_hit, info in steps:
        flow = flow_name
        state = dict(st)
        signal = cmds.flow_signal(flow, state, text, ref_hit=ref_hit, info=info)
        if signal == "collect" and flow == "guided_broken":
            # kumpulkan kategori dari teks (cermin _consume_guided_broken)
            cat = ""
            for tok in text.lower().split():
                if len(tok) >= 3 and tok not in ("yang", "rusak", "cari", "apa"):
                    cat = tok
                    break
            res = cmds.flow_next(flow, state, signal,
                                 {"category": cat} if cat else {})
        elif signal == "correction":
            res = cmds.flow_next(flow, state, signal,
                                 {"asset_refs": [cmds.detect_correction(text)]})
        else:
            res = cmds.flow_next(flow, state, signal)
        actions.append(res.get("action"))
        if res.get("action") in ("ask", "restart", "resume"):
            state = {"step": res.get("step") or state.get("step"),
                     "slots": res.get("slots") or {}}
    return actions


class TestRepairDetection(unittest.TestCase):
    def test_cancel_words(self):
        for t in ["batal", "batalkan ya", "gajadi deh", "cancel", "tidak jadi"]:
            self.assertTrue(cmds.detect_cancel(t), t)

    def test_cancel_never_on_fulfilled(self):
        # regresi penting: status form bukan pembatalan
        for t in ["material yang sudah fulfilled", "sudah selesai",
                  "yang belum resolved", "cukup sekian"]:
            self.assertFalse(cmds.detect_cancel(t), t)

    def test_resume_words(self):
        for t in ["lanjut", "lanjutkan", "kembali"]:
            self.assertTrue(cmds.detect_resume(t), t)
        self.assertFalse(cmds.detect_resume("rekap aset"))

    def test_correction_payload(self):
        self.assertEqual(cmds.detect_correction("ralat PRN-02"), "PRN-02")
        self.assertEqual(cmds.detect_correction("maksud saya ITLT-008"), "ITLT-008")
        self.assertEqual(
            cmds.detect_correction("bukan ITLT-007, maksudnya ITLT-008"), "ITLT-008")
        self.assertEqual(cmds.detect_correction("siapa pakai ITLT-007"), "")

    def test_show_all(self):
        for t in ["semua", "ya", "tampilkan semua"]:
            self.assertTrue(cmds.detect_show_all(t), t)


class TestCommandSchema(unittest.TestCase):
    def test_valid_commands(self):
        self.assertTrue(
            cmds.build_command("asset_detail", {"asset_refs": ["ITLT-007"]}, {})["valid"])
        self.assertTrue(
            cmds.build_command("check_stock", {}, {})["valid"])

    def test_invalid_commands(self):
        bad = cmds.build_command("asset_detail", {}, {})
        self.assertFalse(bad["valid"])
        self.assertTrue(any("missing slot" in e for e in bad["errors"]))
        bad2 = cmds.build_command("asset_search", {"state": "heng"}, {})
        self.assertFalse(bad2["valid"])
        bad3 = cmds.build_command("greeting", {}, {})
        self.assertFalse(bad3["valid"])

    def test_unknown_slots_dropped(self):
        got = cmds.build_command("recap", {"item": "x", "asset_refs": ["A-1"]}, {})
        self.assertTrue(got["valid"])
        self.assertEqual(got["slots"], {})


class TestStories(unittest.TestCase):
    def test_confirm_then_all(self):
        actions = run_story([
            ("confirm_asset", {"step": "await_choice", "slots": {}}, "semua", False, ""),
        ])
        self.assertEqual(actions, ["show_all"])

    def test_confirm_correct_then_one(self):
        actions = run_story([
            ("confirm_asset", {"step": "await_choice", "slots": {}}, "ralat PRN-02", False, ""),
        ])
        # koreksi -> restart di step yang sama dengan slot baru
        self.assertEqual(actions, ["restart"])

    def test_confirm_cancel(self):
        actions = run_story([
            ("confirm_asset", {"step": "await_choice", "slots": {}}, "batal", False, ""),
        ])
        self.assertEqual(actions, ["close"])

    def test_confirm_new_topic_push(self):
        actions = run_story([
            ("confirm_asset", {"step": "await_choice", "slots": {}}, "stok tinta berapa", False, ""),
        ])
        # sinyal new_topic -> aksi push (flow lama ke stack, topik baru jalan)
        self.assertEqual(actions, ["push"])

    def test_unit_info_branch(self):
        actions = run_story([
            ("unit_clarify", {"step": "await_info", "slots": {}}, "mereknya apa", False, "info_brand"),
        ])
        self.assertEqual(actions, ["info_brand"])

    def test_guided_broken_collect_execute(self):
        actions = run_story([
            ("guided_broken", {"step": "await_category", "slots": {}}, "laptop", False, ""),
        ])
        self.assertEqual(actions, ["execute"])

    def test_guided_broken_empty_ask_again(self):
        actions = run_story([
            ("guided_broken", {"step": "await_category", "slots": {}}, "yang", False, ""),
        ])
        self.assertEqual(actions, ["ask"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
