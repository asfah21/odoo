# -*- coding: utf-8 -*-
"""Ask AI backend — pipeline ala WACS (Go) di atas ORM Odoo.

Alur per pesan (cermin ``processWithAgent`` + ``processMessageFallback`` WACS)::

    USER
      ↓
    [L1] llm_decide() → None (nonaktif; titik ekstensi, kontrak Decide WACS)
      ↓
    [fast-path] rule sosial (greeting/thanks/goodbye/help) → jawaban canned,
                tanpa tool (cermin ``deterministic_short_circuit``)
      ↓
    [fast-path] ood_rule → scope reply bervariasi, tanpa tool
                (cermin ``deterministic_ood_route``)
      ↓
    [L2] override intent kuat → tool langsung (confidence 1.0)
      ↓
    [L3] classify penuh → route(confidence): handover / klarifikasi / tool
      ↓
    tool ORM → evidence → HTML (grounded: HANYA dari data, tanpa fabrikasi)
      ↓
    feedback: pesan tak-yakin masuk antrean kurasi (cermin WACS §17)

Prinsip yang dipertahankan dari WACS:
- NLU (``ask_ai_nlu``) tidak pernah menyentuh database.
- Tool write TIDAK ADA — Ask AI read-only; semua perubahan tetap lewat form
  Odoo (lebih ketat dari WACS yang punya create/cancel order: di sini tidak
  ada state yang boleh diubah chat).
- ``route`` memakai floor = min(0.70, threshold) seperti WACS.
"""

import html as _html
import json as _json
import logging
import re as _re
import time as _time
import datetime as _datetime
import urllib.request as _urlrequest

from odoo import api, fields, models

from . import ask_ai_nlu as nlu
from . import ask_ai_commands as cmds
from . import ask_ai_persona as persona

_logger = logging.getLogger(__name__)

# Cooldown ala WACS (agent.unavailableCooldown): satu request gagal/timeout
# tidak boleh membuat tiap pesan berikutnya menunggu timeout penuh.
_LLM_COOLDOWN_UNTIL = 0.0
_LLM_COOLDOWN_SEC = 60.0

# Default parameter — cermin variabel WACS (WACS_QWEN_URL/MODEL/TIMEOUT_SEC).
# Ditambah rephrase_* (Qwen 0.6B hanya poles bahasa, bukan decide).
_LLM_DEFAULTS = {
    "it_asset.ask_ai.llm_enabled": "False",
    "it_asset.ask_ai.llm_url": "http://127.0.0.1:8081",
    "it_asset.ask_ai.llm_model": nlu.LLM_MODEL_DEFAULT,
    "it_asset.ask_ai.llm_timeout": "10",
    "it_asset.ask_ai.llm_json_mode": "True",
    "it_asset.ask_ai.rephrase_enabled": "False",
    "it_asset.ask_ai.rephrase_max_tokens": "150",
    "it_asset.ask_ai.rephrase_temperature": "0.7",
}

# Cache kamus di memori (TTL singkat) agar tiap pesan tidak query 2000 baris.
# Key: dbname -> (expire_epoch, rows). Rows: list[(term, normalized, kind)].
_DICT_CACHE = {}
_DICT_CACHE_TTL = 120.0


def _levenshtein_le2(a, b, limit=2):
    """Edit distance dengan early-exit > limit (murah untuk typo 1-2 huruf)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > limit:
        return limit + 1
    if la == 0 or lb == 0:
        return max(la, lb)
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        row_min = cur[0]
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if cur[j] < row_min:
                row_min = cur[j]
        if row_min > limit:
            return limit + 1
        prev = cur
    return prev[lb]

_ASSET_FIELDS = [
    "name", "asset_tag", "asset_type", "it_type", "category_id",
    "product_id", "lot_id", "employee_id", "unit_id", "state",
    "condition", "usage_type", "model", "specification",
]
_CONSUMABLE_FIELDS = ["name", "product_id", "qty_available", "min_quantity", "uom_id"]
_ASSIGN_FIELDS = ["asset_id", "employee_id", "assignment_date", "return_date", "state"]
_MAINT_FIELDS = ["asset_id", "maintenance_date", "maintenance_type",
                 "description", "cost", "technician"]

_STATE_LABEL = {"available": "Tersedia", "in_use": "Dipakai",
                "maintenance": "Out of Service", "retired": "Retired"}
_COND_LABEL = {"good": "Good", "degraded": "Degraded", "broken": "Broken"}
_ASSET_TYPE_LABEL = {"it": "IT", "operation": "Operasional"}
_IT_TYPE_LABEL = {"asset": "Asset", "accessory": "Accessory",
                  "spare_part": "Spare Part", "tool": "Tool",
                  "consumable": "Consumable"}
_FORM_STATE_LABEL = {
    "draft": "Draft", "submitted": "Diajukan", "approved": "Disetujui",
    "partially_fulfilled": "Sebagian Terpenuhi", "fulfilled": "Fulfilled",
    "rejected": "Ditolak", "signed": "Signed",
    "confirmed": "Dikonfirmasi", "resolved": "Resolved",
    "open": "Belum selesai", "done": "Sudah selesai",
}
_PERIOD_LABEL = {"today": "hari ini", "yesterday": "kemarin",
                 "this_week": "minggu ini", "this_month": "bulan ini",
                 "last_month": "bulan lalu"}
_DAMAGE_TYPE_WORDS = {
    "physical": ("fisik", "pecah", "hancur", "patah", "retak"),
    "system": ("sistem", "software", "aplikasi", "lemot", "error"),
    "lost": ("hilang", "lost", "lenyap"),
}
_DAMAGE_TYPE_LABEL = {"physical": "Fisik", "system": "Sistem/Software",
                      "lost": "Hilang", "other": "Lainnya"}

# V2 (goals2.md §9): kategori fleet/unit — dicari ke it_asset.unit, bukan
# category_id aset (yang hanya berisi Laptop/Desktop/Printer/Radio Rig).
_FLEET_CATEGORIES = {"excavator", "dump truck", "water truck",
                     "light vehicle", "dozer", "grader", "fleet", "unit"}
_UNIT_STATE_MAP = {"ready": "Ready", "standby": "Standby",
                   "breakdown": "Breakdown"}


def _or_domain(conds):
    """Bangun domain OR Odoo dari list kondisi: ['|']*(n-1) + conds."""
    conds = list(conds)
    if not conds:
        return []
    if len(conds) == 1:
        return conds
    return ["|"] * (len(conds) - 1) + conds


# Jawaban lanjutan atas pertanyaan konfirmasi ("yang mana: tag spesifik atau
# semua?"). Hanya dimaknai bila sesi punya pending; kalau tidak, teks jalan
# normal seperti biasa.
_RE_SHOW_ALL = _re.compile(
    r"^\W*(tampilkan\s+semua|tampil\s+semua|lihat\s+semua|tunjukkan\s+semua|"
    r"semua|semuanya|all|ya|iya|oke|ok|mau|boleh)(\W*)$", _re.I)
_PENDING_ACTIONS = (nlu.INTENT_ASSET_USER, nlu.INTENT_ASSET_DETAIL,
                     nlu.INTENT_ASSET_HISTORY, nlu.INTENT_UNIT_DETAIL)
_PENDING_CAP = 30  # id terbanyak yang diingat untuk "semua"

_UNIT_FIELDS = ["name", "category_id", "brand", "model", "state", "remarks"]

# Kata penanya info unit yang spesifik (selain itu = kode polos -> klarifikasi).
_SPECIFIC_UNIT_WORDS = ("merek", "merk", "brand", "model", "status",
                        "kondisi", "aset", "radio", "terpasang", "pasang",
                        "riwayat", "sejarah", "history", "siapa", "pakai",
                        "pengguna", "tahun", "km", "kilometer", "bbm")
_GENERIC_UNIT_WORDS = frozenset(
    "cari carikan lihat tampil tampilkan info informasi detail data "
    "tentang apa itu ini tersebut dong kah unit fleet yang di ke dari "
    "untuk berapa ada saya kak min mas mbak pak bu tolong mohon".split())

_SOCIAL_INTENTS = (nlu.INTENT_GREETING, nlu.INTENT_THANKS,
                   nlu.INTENT_GOODBYE, nlu.INTENT_HELP,
                   nlu.INTENT_IDENTITY, nlu.INTENT_CREATOR)

# Jawaban canned bervariasi (rotasi deterministik) biar tak terasa template.
# {daypart} diisi pagi/siang/sore/malam dari jam server.
_CANNED_SOCIAL = {
    nlu.INTENT_GREETING: [
        "Selamat {daypart}! 👋 Saya membaca <b>data live modul IT</b> — "
        "stok, aset, pengguna, kondisi, dan riwayat.<br/>Coba: "
        "<i>“stok radio ht?”</i> • <i>“rekap aset”</i> • "
        "<i>“siapa yang pakai ITLT-007?”</i>",
        "Halo! 👋 Ada yang bisa saya bantu dari data IT?<br/>Misal: "
        "<i>“aset tersedia apa saja?”</i> • <i>“riwayat DT-02?”</i>",
        "Hai! 😊 Saya siap bantu cek data live IT — stok, aset, pengguna, "
        "sampai riwayat.<br/>Tanya saja, mis: <i>“rekap aset”</i>",
    ],
    nlu.INTENT_THANKS: [
        "Sama-sama! 🙏 Ada lagi yang mau dicek dari data IT?",
        "Siap, sama-sama! 👍 Kabari saja kalau butuh cek data lagi.",
        "Dengan senang hati! 🙏 Saya di sini kalau dibutuhkan.",
    ],
    nlu.INTENT_GOODBYE: [
        "Siap, sampai jumpa! 👋 Saya di menu <b>IT → Ask AI</b> kalau dibutuhkan lagi.",
        "Oke, sampai ketemu lagi! 👋 Semoga harinya lancar.",
    ],
    nlu.INTENT_HELP: [
        "Yang bisa saya jawab dari <b>data live</b>:<br/>"
        "<div class='ai-help'>"
        "<div>📦 <b>Stok</b> — <i>“stok radio ht?”</i>, <i>“stok menipis”</i></div>"
        "<div>💻 <b>Jumlah</b> — <i>“rekap aset”</i>, <i>“laptop tersedia”</i></div>"
        "<div>👤 <b>Pengguna</b> — <i>“siapa pakai ITLT-007?”</i></div>"
        "<div>📜 <b>Riwayat</b> — <i>“riwayat PRN-01”</i></div>"
        "<div>🚜 <b>Unit</b> — <i>“DT-02 merek apa?”</i></div>"
        "</div>",
        "Saya bisa bantu soal: <b>stok</b>, <b>rekap aset</b>, "
        "<b>pengguna</b>, <b>riwayat</b>, <b>unit/fleet</b>, "
        "<b>handover</b>, dan <b>request</b> — semua dari data live.<br/>"
        "Contoh: <i>“radio ht yang tersedia?”</i>",
    ],
    nlu.INTENT_IDENTITY: [
        "Saya <b>GSI IT Assistant (Ask AI)</b> 🤖 — asisten inventaris IT "
        "<b>PT GSI, Site Wolo</b>.<br/>Fungsi saya: menjawab dari <b>data live "
        "modul IT</b> — stok, aset, pengguna, kondisi, riwayat, maintenance, "
        "handover, damage, dan request.<br/>Coba: <i>“rekap aset”</i> • "
        "<i>“stok radio ht?”</i> • <i>“siapa pakai ITLT-007?”</i>",
        "Saya asisten IT di sini! 🤖 Tugas saya membantu cek <b>data live "
        "inventaris</b> — stok, aset, pengguna, sampai riwayat.<br/>Ada yang "
        "mau ditanyakan?",
    ],
    nlu.INTENT_CREATOR: [
        "Saya dibuat oleh <b>Azvan</b> — <b>IT Department PT GSI "
        "(Site Wolo)</b> 🛠️<br/>Saya berjalan lokal di server "
        "(Qwen3-0.6B + orkestrasi WACS) tanpa API eksternal.<br/>Ada masukan? "
        "Sampaikan ke tim IT Site Wolo ya.",
    ],
}


def _daypart():
    h = _datetime.datetime.now().hour
    if 4 <= h < 11:
        return "pagi"
    if 11 <= h < 15:
        return "siang"
    if 15 <= h < 19:
        return "sore"
    return "malam"


def _canned_social(intent, seed=""):
    """Ambil varian canned + isi {daypart}; rotasi deterministik."""
    pool = _CANNED_SOCIAL.get(intent) or [""]
    pick = pool[nlu._pick_variant("%s|%s" % (intent, seed or ""), len(pool))]
    if "{daypart}" in pick:
        pick = pick.replace("{daypart}", _daypart())
    return pick


def _esc(value):
    return _html.escape(str(value if value is not None else ""), quote=True)


def _m2o(value):
    if not value:
        return ""
    return value[1] if isinstance(value, (list, tuple)) else str(value)


class ITAskAI(models.AbstractModel):
    """Mesin jawab Ask AI. Abstract (tanpa tabel) — NLU + orkestrasi tool."""
    _name = "it_asset.ask_ai"
    _description = "Ask AI Answer Engine (WACS-method)"

    # ------------------------------------------------------------------
    # entrypoint (dipanggil JS via orm.call)
    # ------------------------------------------------------------------
    @api.model
    def answer(self, question, session_id=None, mode="alpha"):
        """Jawab satu pertanyaan + simpan otomatis ke riwayat (retensi 10 hari).

        ``mode``: "alpha" (rule/NLU + kamus, perilaku sekarang) atau "beta"
        (Qwen decide + dictionary + DB only, untuk eksperimen). Mode selain
        itu dinormalisasi ke "alpha". Mode ikut dikembalikan di ``out["mode"]``
        agar frontend bisa menandainya (tidak di-pop).

        Kompatibel mundur: JS lama yang memanggil ``answer([text])`` tetap
        jalan (sesi baru dibuat otomatis). Mengembalikan dict siap-render
        ``{html, intent, confidence, method, action?, session_id, mode}``.
        """
        mode = "beta" if (mode or "") == "beta" else "alpha"
        try:
            consumed = self._consume_flow(question, session_id)
        except Exception as exc:  # flow tak boleh merusak jawaban
            _logger.warning("Ask AI flow gagal dibaca: %s", exc)
            consumed = None
        if consumed is not None:
            out = consumed
        elif mode == "beta":
            out = self._answer_beta(question, session_id)
        else:
            out = self._answer_inner(question, session_id=session_id)
        out["mode"] = out.get("mode") or mode
        try:
            out["session_id"] = self._history_save(
                question, out, session_id=session_id)
        except Exception as exc:  # riwayat tak boleh merusak jawaban
            _logger.warning("Ask AI history gagal disimpan: %s", exc)
            out["session_id"] = session_id
        for key in ("pending_action", "pending_ids", "pending_label",
                    "flow_name", "flow_step", "flow_slots", "flow_stack",
                    "flow_clear"):
            out.pop(key, None)
        return out

    def _preprocess(self, question):
        """Preprocess bersama Alpha & Beta: empty/F4/persona/kamus/OOD-awal.

        Kembalikan dict {text (efektif/terkoreksi), lang, pkey, pname,
        dict_hint, ood0_reason, is_ood0, early}. ``early`` = out jawaban
        langsung (empty / self_correct / name-only greeting) atau None.
        Riwayat/feedback pemanggil tetap memakai pertanyaan asli.
        """
        text = (question or "").strip()
        if not text:
            return {"early": self._out(
                text, nlu.INTENT_UNKNOWN, 0.0, "empty",
                "Tulis dulu pertanyaannya 🙂", "clarification")}

        # F4: teguran halu -> minta maaf + klarifikasi (masuk kurasi).
        # Dicek paling awal agar keluhan user tak tenggelam dalam flow.
        if cmds.detect_self_correct(text):
            out = self._out(
                text, nlu.INTENT_UNKNOWN, 0.0, "self_correct",
                "Maaf, saya salah — terima kasih sudah mengoreksi! 🙏"
                "<br/>Biar saya bantu dengan benar: sebutkan <b>kode tag / "
                "nama barangnya</b> dengan kata-katamu sendiri, atau ketik "
                "<i>“rekap aset”</i> untuk mulai dari ringkasan.",
                "clarification",
                suggestions=["rekap aset", "stok radio ht", "bantuan"])
            out["flow_clear"] = True
            self._record_feedback(text, out)
            return {"early": out}

        # -1) Persona: bahasa user + kupas panggilan nama ("Alya, stok..?").
        try:
            pkey, pname = self._persona_ctx()
        except Exception:
            pkey, pname = "alya", "Alya"
        lang = persona.detect_language(text)
        stripped = persona.strip_persona_name(text, [pname, "Alya", "Raka"])
        if stripped != text:
            text = stripped
        if not text:
            # user hanya memanggil nama -> sapa sebagai greeting
            return {"early": self._out(
                question or "", nlu.INTENT_GREETING, 0.95, "persona_name",
                self._social_reply(nlu.INTENT_GREETING, "", lang, pkey, pname),
                "deterministic_answer")}

        # 0) Kamus lokal: koreksi typo ringan tanpa token/API/Qwen.
        #    Mis. "stokc radiio ht" -> tahu maksud "stok radio ht".
        # F3b: vonis OOD dihitung pada teks PRA-kamus agar tak bisa
        # dikalahkan oleh koreksi kamus / NLU lemah / LLM ragu-ragu.
        try:
            ood0_reason, is_ood0 = nlu.ood_match(text)
        except Exception:
            ood0_reason, is_ood0 = "", False
        try:
            dict_hint = self._dict_assist(text)
        except Exception as exc:
            _logger.warning("Ask AI dict-assist gagal: %s", exc)
            dict_hint = {}
        effective_text = dict_hint.get("effective_text") or text
        # Tool/NLU bekerja pada teks terkoreksi kamus (typo sudah diluruskan),
        # sedangkan riwayat/feedback tetap memakai teks asli dari pemanggil.
        return {"text": effective_text, "lang": lang, "pkey": pkey,
                "pname": pname, "dict_hint": dict_hint,
                "ood0_reason": ood0_reason, "is_ood0": is_ood0, "early": None}

    def _answer_beta(self, question, session_id=None):
        """Jalur Beta (eksperimen MURNI): Qwen decide + dictionary + DB only.

        Tanpa rule/override/NLU-TFIDF dan TANPA fallback ke Alpha. Bila Qwen
        mati/tak menjawab, kembalikan pesan jujur (bukan jawaban Alpha yang
        menyamar). Flow konfirmasi, guard F1/F2/F4, grounding, dan rephrase
        dipakai sama seperti Alpha (lapisan keamanan & UX, bukan pendekatan
        pemahaman).
        """
        pre = self._preprocess(question)
        if pre.get("early") is not None:
            out = pre["early"]
            out["mode"] = "beta"
            return out
        text = pre["text"]
        lang, pkey, pname = pre["lang"], pre["pkey"], pre["pname"]
        dict_hint = pre["dict_hint"]
        _ood0_reason, _is_ood0 = pre["ood0_reason"], pre["is_ood0"]

        try:
            llm_cfg = self._get_llm_config()
        except Exception:
            llm_cfg = {"enabled": False}
        if not llm_cfg.get("enabled"):
            out = self._out(
                text, nlu.INTENT_UNKNOWN, 0.0, "beta_no_qwen",
                "Mode <b>Beta</b> butuh Qwen Decide yang aktif — saat ini mati. "
                "Aktifkan di <b>AI → Setting</b> (Qwen Decide) + jalankan "
                "llama-server, atau pakai mode <b>Alpha</b> yang tak butuh Qwen.",
                "beta_no_qwen",
                suggestions=["rekap aset", "stok radio ht", "bantuan"])
            out["mode"] = "beta_no_qwen"
            self._record_feedback(text, out)
            return out

        decision = self._try_llm_decide(text)
        if not decision:
            # Beta murni: tanpa Qwen tidak ada tebakan maksud -> jujur buntu,
            # JANGAN fallback ke Alpha (mengotori data eksperimen).
            _logger.info("Ask AI Beta: Qwen tak menjawab -> buntu jujur")
            out = self._out(
                text, nlu.INTENT_UNKNOWN, 0.0, "beta_no_qwen",
                "Mode <b>Beta</b> butuh Qwen yang hidup — llama-server tidak "
                "menjawab. Cek service <b>llm</b> / Test Qwen di Setting, lalu "
                "kirim ulang pesanmu. (Tanpa Qwen, Beta tidak bisa menebak "
                "maksud. Pakai <b>Alpha</b> untuk jalur non-Qwen.)",
                "beta_no_qwen",
                suggestions=["rekap aset", "stok radio ht", "bantuan"])
            out["flow_clear"] = True
            out["mode"] = "beta_no_qwen"
            self._record_feedback(text, out)
            return out

        intent, conf = decision["intent"], decision["confidence"]

        # Sosial via Qwen -> canned persona (pemahaman milik Qwen).
        if intent in _SOCIAL_INTENTS:
            out = self._out(text, intent, conf, "llm",
                             self._social_reply(intent, text, lang, pkey, pname),
                             "deterministic_answer")
            out["mode"] = "beta"
            return out

        ctx_update = {"ask_ai_llm": {
            "entities": decision["entities"] or {},
            "constraints": decision["constraints"] or {},
        }}
        try:
            sctx = self._get_session_ctx(session_id)
        except Exception as exc:
            _logger.warning("Ask AI session-ctx gagal dibaca: %s", exc)
            sctx = {}
        if sctx:
            ctx_update["ask_ai_ctx"] = sctx
        runner = self.with_context(**ctx_update)

        # F3b: OOD pra-kamus menang atas LLM yang tidak EXECUTE.
        if _is_ood0 and conf < nlu.confidence_threshold(intent):
            _logger.info("Ask AI Beta OOD-precedence (conf=%.3f)", conf)
            out = self._ood_scope_out(text, _ood0_reason)
            out["mode"] = "beta"
            self._record_feedback(text, out)
            return out

        entities, constraints = runner._merged_entities(text)
        cmd = cmds.build_command(intent, entities, constraints)
        grounding = {"mode": "beta",
                     "dict_matches": dict_hint.get("matches", {}),
                     "command": cmd["command"], "slots": cmd["slots"],
                     "slot_errors": cmd["errors"]}
        if not cmd["valid"]:
            out = self._out(text, intent, conf, "command_repair",
                             nlu.clarification_reply(intent, entities)
                             + "<div class='ai-sub'>Detail: %s.</div>"
                             % _esc("; ".join(cmd["errors"])),
                             "clarification",
                             suggestions=self._suggest_for(intent))
            out["grounding"] = grounding
            out["mode"] = "beta"
            self._record_feedback(text, out)
            return out
        slot_out = runner._validate_slots_db(text, intent, entities,
                                             original=question)
        if slot_out is not None:
            slot_out["grounding"] = grounding
            slot_out["mode"] = "beta"
            self._record_feedback(text, slot_out)
            return slot_out

        handler = self._TOOLS.get(intent)
        if not handler:
            out = self._out(text, nlu.INTENT_UNKNOWN, 0.0, "llm",
                             nlu.scope_reply(text, ""), "unknown_fallback",
                             suggestions=self._suggest_for(intent))
            out["mode"] = "beta"
            self._record_feedback(text, out)
            return out
        try:
            result = handler(runner, text)
        except Exception as exc:
            _logger.warning("Ask AI Beta tool %s gagal: %s", intent, exc)
            out = self._out(text, intent, conf, "llm",
                             "Maaf, saya gagal membaca data untuk itu. "
                             "Coba lagi atau persempit kata kuncinya.",
                             "tool_error",
                             suggestions=self._suggest_for(intent))
            out["mode"] = "beta"
            self._record_feedback(text, out)
            return out
        if result is None:
            entities2, _c2 = runner._merged_entities(text)
            out = self._out(text, intent, conf, "llm",
                             nlu.clarification_reply(intent, entities2),
                             "clarification",
                             suggestions=self._suggest_for(intent))
            out["mode"] = "beta"
            self._record_feedback(text, out)
            return out
        if result.get("miss"):
            out = self._out(text, intent, conf, "llm",
                             nlu.data_miss_reply(text)
                             + (result.get("hint") or ""), "data_miss",
                             suggestions=self._suggest_for(intent))
            out["grounding"] = grounding
            out["mode"] = "beta"
            self._record_feedback(text, out)
            return out
        out = self._out(text, intent, conf, "llm", result["html"],
                         result.get("tool", intent),
                         action=result.get("action"),
                         suggestions=result.get("suggestions"))
        out["grounding"] = grounding
        out["mode"] = "beta"
        _logger.info("Ask AI Beta grounding intent=%s conf=%.3f slots=%s",
                     intent, conf, sorted(cmd["slots"]))
        try:
            polished = self._maybe_rephrase(out.get("html") or "", intent, lang)
            if polished:
                out["html"] = polished
                out["rephrased"] = True
        except Exception as exc:
            _logger.warning("Ask AI rephrase gagal, pakai jawaban asli: %s", exc)
        self._record_feedback(text, out)
        return out

    def _answer_inner(self, question, session_id=None):
        """Jalur Alpha: rule/NLU deterministik + kamus (perilaku sekarang).

        Selalu kembalikan dict siap-render. Lihat ``answer()`` untuk kontrak.
        """
        pre = self._preprocess(question)
        if pre.get("early") is not None:
            return pre["early"]
        text = pre["text"]
        lang, pkey, pname = pre["lang"], pre["pkey"], pre["pname"]
        dict_hint = pre["dict_hint"]
        effective_text = text
        _ood0_reason, _is_ood0 = pre["ood0_reason"], pre["is_ood0"]

        # Klasifikasi deterministik dulu: fast-path sosial/OOD dan L2
        # override TIDAK PERNAH memanggil Qwen (cermin WACS — tiap panggilan
        # Qwen adalah round-trip CPU mahal di runtime single-slot).
        classification = nlu.classify(effective_text)
        method = classification["method"]
        if dict_hint.get("corrected") and method in ("empty",):
            # Kamus menemukan topik jelas tapi NLU masih kosong:
            # naikkan ke klarifikasi terarah, bukan handover buta.
            classification = {"intent": dict_hint.get("suggested_intent") or nlu.INTENT_ASSET_SEARCH,
                              "confidence": 0.72,
                              "category": nlu.get_intent_category(
                                  dict_hint.get("suggested_intent") or nlu.INTENT_ASSET_SEARCH),
                              "method": "dict", "ood_reason": ""}
            method = "dict"

        if method not in ("rule", "ood_rule", "override"):
            llm_classification = self._try_llm_decide(text)
            if llm_classification:
                classification = llm_classification
                method = "llm"

        # F6b: tebakan sosial LLM tanpa jangkar kata -> turunkan ke klarifikasi.
        # (Qwen 0.6B kadang menebak 'identity' untuk gumaman bingung.)
        if method == "llm" and classification.get("intent") in _SOCIAL_INTENTS:
            if not cmds.social_grounded(classification["intent"], text):
                _logger.info("Ask AI llm-social ditolak (ungrounded): %s",
                             classification["intent"])
                classification = {"intent": nlu.INTENT_UNKNOWN,
                                  "confidence": 0.72,
                                  "category": nlu.CAT_OTHER,
                                  "method": "llm_ungrounded",
                                  "ood_reason": ""}
                method = "llm_ungrounded"

        intent = classification["intent"]
        conf = classification["confidence"]
        method = classification["method"]

        # Fast-path sosial: jawaban canned bervariasi, tanpa tool (cermin WACS).
        # Persona: varian English bila user berbahasa Inggris; identity
        # selalu memakai nama persona aktif.
        if method == "rule" and intent in _SOCIAL_INTENTS:
            return self._out(text, intent, conf, "deterministic_answer",
                             self._social_reply(intent, text, lang, pkey, pname),
                             "deterministic_answer")

        # Fast-path OOD: tolak bervariasi, tanpa tool (cermin WACS).
        if method == "ood_rule":
            reason = classification.get("ood_reason", "")
            out = self._ood_scope_out(text, reason)
            self._record_feedback(text, out)
            return out

        # Entitas LLM (bila ada) hanya dipakai bila grounded — substring
        # dari teks user (cermin WACS: extractor pemilik kanal entitas,
        # model tak boleh mengarang nilai).
        ctx_update = {}
        if method == "llm":
            ctx_update["ask_ai_llm"] = {
                "entities": classification.get("llm_entities") or {},
                "constraints": classification.get("llm_constraints") or {},
            }
        try:
            sctx = self._get_session_ctx(session_id)
        except Exception as exc:
            _logger.warning("Ask AI session-ctx gagal dibaca: %s", exc)
            sctx = {}
        if sctx:
            ctx_update["ask_ai_ctx"] = sctx
        runner = self.with_context(**ctx_update) if ctx_update else self

        # E: jejak grounding untuk audit/kurasi (kamus apa yang dipakai).
        grounding = {"dict_matches": dict_hint.get("matches", {})}

        # F: pencarian rusak terpandu — item tanpa kategori ("kabel rusak"
        # tanpa kategori jelas) ditanya kategorinya dulu via flow data-driven,
        # bukan daftar semua aset rusak. "aset rusak" polos tetap langsung.
        if intent == nlu.INTENT_ASSET_SEARCH:
            try:
                pre_entities = runner._merged_entities(text)[0]
            except Exception:
                pre_entities = {}
            if (pre_entities.get("condition") == "broken"
                    and not pre_entities.get("category")
                    and not pre_entities.get("asset_refs")
                    and (pre_entities.get("item") or "")
                    and len(pre_entities.get("item") or "") >= 3):
                prompt = cmds.FLOW_DEFS["guided_broken"]["prompts"]["await_category"]
                out = self._out(text, intent, conf, "flow_start", prompt,
                                 "guided_broken",
                                 suggestions=["laptop", "printer", "radio", "cctv"])
                out.update(self._flow_keys("guided_broken", "await_category", {}))
                out["grounding"] = grounding
                self._record_feedback(text, out)
                return out

        route = nlu.route(classification)
        # F3b: OOD pra-kamus menang atas jalur lemah/ragu (dict/NLU/LLM-ragu).
        # Rule/override/LLM-yakin tidak tersentuh.
        if _is_ood0 and cmds.ood_wins_over(method, route == nlu.ROUTE_EXECUTE):
            _logger.info("Ask AI OOD-precedence menang atas method=%s", method)
            out = self._ood_scope_out(text, _ood0_reason)
            self._record_feedback(text, out)
            return out
        if route == nlu.ROUTE_HANDOVER:
            entities, _constraints = self._merged_entities(text)
            if nlu.has_meaningful_signal(entities, text):
                # Tak yakin TAPI paham topiknya -> tanya balik spesifik dulu,
                # jangan langsung lempar ke staff.
                out = self._out(text, intent, conf, method,
                                nlu.clarification_reply(intent, entities),
                                "clarification",
                                suggestions=self._suggest_for(intent))
            else:
                out = self._out(text, intent, conf, method,
                                nlu.handover_reply(text), "handover_to_staff",
                                handoff=True,
                                suggestions=self._suggest_for(intent))
            self._record_feedback(text, out)
            return out
        if route == nlu.ROUTE_CLARIFY:
            entities, _constraints = self._merged_entities(text)
            out = self._out(text, intent, conf, method,
                            nlu.clarification_reply(intent, entities),
                            "clarification",
                            suggestions=self._suggest_for(intent))
            _logger.info("Ask AI clarify intent=%s method=%s conf=%.3f",
                         intent, method, conf)
            self._record_feedback(text, out)
            return out

        # A+C: bangun command tervalidasi + validasi slot ke DB/kamus
        # SEBELUM tool jalan (pola CALM: perintah tak-valid tak dieksekusi).
        entities, constraints = runner._merged_entities(text)
        cmd = cmds.build_command(intent, entities, constraints)
        grounding.update({"command": cmd["command"], "slots": cmd["slots"],
                          "slot_errors": cmd["errors"]})
        if not cmd["valid"]:
            out = self._out(text, intent, conf, "command_repair",
                             nlu.clarification_reply(intent, entities)
                             + "<div class='ai-sub'>Detail: %s.</div>"
                             % _esc("; ".join(cmd["errors"])),
                             "clarification",
                             suggestions=self._suggest_for(intent))
            out["grounding"] = grounding
            _logger.info("Ask AI grounding intent=%s cmd=%s valid=False errors=%s",
                         intent, cmd["command"], cmd["errors"])
            self._record_feedback(text, out)
            return out
        slot_out = runner._validate_slots_db(text, intent, entities,
                                                 original=question)
        if slot_out is not None:
            slot_out["grounding"] = grounding
            _logger.info("Ask AI grounding intent=%s cmd=%s slot_repair",
                         intent, cmd["command"])
            self._record_feedback(text, slot_out)
            return slot_out

        # Eksekusi tool sesuai intent (cermin routeDecision WACS).
        handler = self._TOOLS.get(intent)
        if not handler:
            out = self._out(text, nlu.INTENT_UNKNOWN, 0.0, method,
                            nlu.scope_reply(text, ""), "unknown_fallback",
                            suggestions=self._suggest_for(intent))
            self._record_feedback(text, out)
            return out
        try:
            result = handler(runner, text)
        except Exception as exc:  # tool gagal → fallback aman, bukan karangan
            _logger.warning("Ask AI tool %s gagal: %s", intent, exc)
            out = self._out(text, intent, conf, method,
                            "Maaf, saya gagal membaca data untuk itu. "
                            "Coba lagi atau persempit kata kuncinya.",
                            "tool_error",
                            suggestions=self._suggest_for(intent))
            self._record_feedback(text, out)
            return out

        if result is None:  # tool butuh klarifikasi (entitas kurang)
            entities, _constraints = self._merged_entities(text)
            out = self._out(text, intent, conf, method,
                            nlu.clarification_reply(intent, entities),
                            "clarification",
                            suggestions=self._suggest_for(intent))
            self._record_feedback(text, out)
            return out
        if result.get("miss"):  # data tidak ditemukan (cermin storeMiss WACS)
            out = self._out(text, intent, conf, method,
                            nlu.data_miss_reply(text)
                            + (result.get("hint") or ""), "data_miss",
                            suggestions=self._suggest_for(intent))
            out["grounding"] = grounding
            self._record_feedback(text, out)
            return out

        out = self._out(text, intent, conf, method, result["html"],
                        result.get("tool", intent),
                        action=result.get("action"),
                        suggestions=result.get("suggestions"))
        out["grounding"] = grounding
        _logger.info("Ask AI grounding intent=%s method=%s conf=%.3f cmd=%s slots=%s dict=%s",
                     intent, method, conf, cmd["command"], sorted(cmd["slots"]),
                     sorted((dict_hint.get("matches") or {})))
        # Rephrase Qwen (opsional, default mati): poles bahasa tanpa ubah fakta.
        try:
            polished = self._maybe_rephrase(out.get("html") or "", intent, lang)
            if polished:
                out["html"] = polished
                out["rephrased"] = True
        except Exception as exc:
            _logger.warning("Ask AI rephrase gagal, pakai jawaban asli: %s", exc)
        self._record_feedback(text, out)  # no-op bila terjawab yakin
        return out

    # ------------------------------------------------------------------
    # Kamus lokal typo-tolerant (tanpa token API / tanpa Qwen)
    # ------------------------------------------------------------------
    def _dict_rows(self):
        """Ambil baris kamus aktif (cache 120 dtk per database)."""
        try:
            dbname = self.env.cr.dbname
        except Exception:
            dbname = "default"
        now = _time.time()
        hit = _DICT_CACHE.get(dbname)
        if hit and hit[0] > now:
            return hit[1]
        try:
            rows = self.env["it_asset.ask_ai.term"].search_read(
                [("active", "=", True)], ["term", "normalized", "kind"],
                limit=2000)
        except Exception:
            rows = []
        _DICT_CACHE[dbname] = (now + _DICT_CACHE_TTL, rows)
        return rows

    def _dict_assist(self, text):
        """Koreksi typo ringan memakai kamus + suggest intent.

        Kembalikan dict {corrected(bool), effective_text, matches, suggested_intent}.
        Murah: hanya token >= 4 huruf, edit-distance <= 2, dan hanya bila
        kamus sudah di-generate (tanpa kamus -> no-op).
        """
        rows = self._dict_rows()
        if not rows:
            return {}
        norm = nlu.normalize_id(text)
        toks = [t for t in norm.split() if len(t) >= 4]
        if not toks:
            return {}
        # index kata kamus -> (term kanonik, kind)
        word_index = {}
        for r in rows:
            base = (r.get("normalized") or r.get("term") or "").lower()
            for w in base.split():
                if len(w) >= 4 and w not in word_index:
                    word_index[w] = (r.get("term") or w, r.get("kind") or "")
        if not word_index:
            return {}
        corrections = {}
        matched_kinds = set()
        for tok in set(toks):
            if tok in word_index:
                continue
            best, best_kind, best_d = None, "", 99
            for w, (term, kind) in word_index.items():
                if abs(len(w) - len(tok)) > 2:
                    continue
                if not w or not tok or w[0] != tok[0]:
                    # syarat huruf pertama sama: presisi tinggi, murah
                    continue
                d = _levenshtein_le2(tok, w, 2)
                if d < best_d:
                    best, best_kind, best_d = w, kind, d
                    if d <= 1:
                        break
            if best and best_d <= 2:
                corrections[tok] = word_index[best][0].lower()
                if best_kind:
                    matched_kinds.add(best_kind)
        if not corrections:
            return {}
        effective = norm
        for typo, canon in corrections.items():
            effective = _re.sub(r"\b%s\b" % _re.escape(typo), canon, effective)
        # tebak intent dari jenis kamus yang kena
        suggested = ""
        if matched_kinds & {"product", "consumable"}:
            suggested = nlu.INTENT_CHECK_STOCK
        elif matched_kinds & {"asset"}:
            suggested = nlu.INTENT_ASSET_DETAIL
        elif matched_kinds & {"unit", "unit_category"}:
            suggested = nlu.INTENT_UNIT_DETAIL
        elif matched_kinds & {"category"}:
            suggested = nlu.INTENT_ASSET_SEARCH
        elif matched_kinds & {"employee"}:
            suggested = nlu.INTENT_ASSET_USER
        return {"corrected": True, "effective_text": effective,
                "matches": corrections, "suggested_intent": suggested,
                "method": "dict"}

    # ------------------------------------------------------------------
    # Rephrase Qwen 0.6B — poles bahasa, FAKTA TIDAK BOLEH BERUBAH
    # ------------------------------------------------------------------
    def _maybe_rephrase(self, html_answer, intent, lang="id"):
        """Poles jawaban HTML dengan Qwen bila rephrase_enabled=True.

        Aturan keras: hanya gaya bahasa; angka/nama/tag/kode/SN, struktur
        HTML dan tabel WAJIB dipertahankan. Gagal/timeout -> kembalikan "".
        Gaya mengikuti persona aktif (Alya/Raka) + bahasa user.
        """
        try:
            Param = self.env["ir.config_parameter"].sudo()
            enabled = str(Param.get_param(
                "it_asset.ask_ai.rephrase_enabled", "False")).strip().lower() in (
                    "1", "true", "yes")
            if not enabled:
                return ""
            url = (Param.get_param(
                "it_asset.ask_ai.llm_url", "http://127.0.0.1:8081") or "").rstrip("/")
            try:
                max_tokens = int(Param.get_param(
                    "it_asset.ask_ai.rephrase_max_tokens", "150") or 150)
            except (TypeError, ValueError):
                max_tokens = 150
            try:
                temperature = float(Param.get_param(
                    "it_asset.ask_ai.rephrase_temperature", "0.7") or 0.7)
            except (TypeError, ValueError):
                temperature = 0.7
        except Exception:
            return ""
        if _time.time() < _LLM_COOLDOWN_UNTIL:
            return ""
        system = (
            "Kamu editor Bahasa Indonesia yang ramah. Poles TEKS PEMBUKA dan "
            "PENUTUP jawaban berikut agar natural dan tidak robotik. ATURAN KERAS: "
            "jangan ubah angka, nama, kode/tag aset, SN, merek, status, tabel, "
            "dan tag HTML apa pun. Jangan tambah fakta baru. Kembalikan HTML "
            "lengkap yang sudah dipoles, tanpa penjelasan tambahan.")
        try:
            pkey, pname = self._persona_ctx()
        except Exception:
            pkey, pname = "alya", "Alya"
        system += " " + persona.rephrase_persona_block(pkey, pname, lang)
        payload = {
            "temperature": max(0.0, min(1.0, temperature)),
            "max_tokens": max(64, min(1024, max_tokens)),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": html_answer[:3000]},
            ],
        }
        try:
            req = _urlrequest.Request(
                url + "/v1/chat/completions",
                data=_json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            started = _time.time()
            with _urlrequest.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            _logger.info("[AI_TIMING] qwen_rephrase total=%.2fs url=%s",
                         _time.time() - started, url)
            polished = (data["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:
            _logger.warning("Ask AI rephrase gagal: %s", exc)
            return ""
        # Guardrail: tolak hasil yang menghilangkan angka/tag penting.
        if not polished or len(polished) < len(html_answer) * 0.5:
            return ""
        for must in _re.findall(r"[A-Z]{2,}-[0-9]+|\d+", html_answer):
            if must and must not in polished:
                _logger.warning("Ask AI rephrase ditolak: fakta hilang (%s)", must)
                return ""
        # F2: tolak hasil yang MEMUNCULKAN kode/angka baru (anti-addition).
        new_codes = cmds.invented_codes(html_answer, polished)
        if new_codes:
            _logger.warning("Ask AI rephrase ditolak: fakta baru (%s)",
                            ",".join(new_codes[:5]))
            return ""
        return polished

    # ------------------------------------------------------------------
    # L1 — klien Qwen (cermin internal/ai/agent/agent.go WACS)
    #
    # Qwen = language understanding: ia HANYA menebak {intent, confidence}.
    # Entitas tetap milik extractor lokal (dua kanal terpisah, cermin WACS);
    # nilai model dipakai hanya bila grounded (substring dari teks user).
    # Qwen tidak memilih tool, tidak menyentuh database, tidak merangkai
    # jawaban — kalimat jawaban selalu dari tool backend (evidence ORM).
    #
    # Menjalankan Qwen (sama seperti WACS):
    #   1. Unduh model: farpluto/Qwen3-0.6B-Q4_K_M-GGUF (file
    #      qwen3-0.6b-q4_k_m.gguf) — lihat D:/10.PROJECT/wacs AGENTS.md.
    #   2. Jalankan runtime: llama-server -m <file.gguf> --port 8081
    #      --ctx-size 2048 --parallel 1 --reasoning off
    #   3. Di Odoo: Settings → Technical → System Parameters, set
    #      it_asset.ask_ai.llm_enabled = True
    #      (URL/model/timeout opsional: it_asset.ask_ai.llm_url,
    #      it_asset.ask_ai.llm_model, it_asset.ask_ai.llm_timeout,
    #      it_asset.ask_ai.llm_json_mode)
    # Tanpa runtime, L1 selalu menyerah ke L2/L3 (desain, bukan kegagalan).
    # ------------------------------------------------------------------
    def _get_llm_config(self):
        Param = self.env["ir.config_parameter"].sudo()
        cfg = {k: Param.get_param(k, v) for k, v in _LLM_DEFAULTS.items()}
        try:
            cfg["timeout"] = float(cfg["it_asset.ask_ai.llm_timeout"] or 10)
        except ValueError:
            cfg["timeout"] = 10.0
        cfg["enabled"] = str(
            cfg["it_asset.ask_ai.llm_enabled"]).strip().lower() in (
                "1", "true", "yes")
        cfg["json_mode"] = str(
            cfg["it_asset.ask_ai.llm_json_mode"]).strip().lower() in (
                "1", "true", "yes")
        return cfg

    def _llm_transport(self, cfg):
        """POST ke endpoint OpenAI-compatible llama.cpp (stdlib urllib)."""
        url = cfg["it_asset.ask_ai.llm_url"].rstrip("/") + "/v1/chat/completions"
        body = {
            "model": cfg["it_asset.ask_ai.llm_model"],
            "temperature": nlu.LLM_DECIDE_TEMPERATURE,
            "max_tokens": nlu.LLM_MAX_DECISION_TOKENS,
            "messages": [],  # diisi pemanggil via closure di bawah
        }
        if cfg["json_mode"]:
            body["response_format"] = {"type": "json_object"}

        def call(system_prompt, user_text):
            payload = dict(body)
            payload["messages"] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ]
            req = _urlrequest.Request(
                url, data=_json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            started = _time.time()
            try:
                with _urlrequest.urlopen(req, timeout=cfg["timeout"]) as resp:
                    data = _json.loads(resp.read().decode("utf-8"))
            finally:
                _logger.info(
                    "[AI_TIMING] qwen_decide total=%.2fs url=%s",
                    _time.time() - started, url)
            return data["choices"][0]["message"]["content"]

        return call

    def _try_llm_decide(self, text):
        """L1 Decide() → klasifikasi, atau None bila menyerah (cermin WACS:
        error/timeout/keputusan-tak-valid/intent unknown → jatuh ke L3)."""
        global _LLM_COOLDOWN_UNTIL
        try:
            cfg = self._get_llm_config()
        except Exception:
            return None
        if not cfg["enabled"]:
            return None
        nlu.LLM_ENABLED = True
        if _time.time() < _LLM_COOLDOWN_UNTIL:
            return None
        try:
            decision = nlu.llm_decide(
                text, transport=self._llm_transport(cfg))
        except Exception as exc:
            _logger.warning("Ask AI L1 (Qwen) gagal: %s", exc)
            decision = None
        if not decision:
            # Gagal/timeout → cooldown singkat agar satu pesan tak pernah
            # menunggu timeout dua kali (cermin unavailableCooldown WACS).
            _LLM_COOLDOWN_UNTIL = _time.time() + _LLM_COOLDOWN_SEC
            return None
        # F5: jejak keputusan LLM untuk diagnosis halusinasi.
        _logger.info("Ask AI L1 decide intent=%s conf=%.3f entities=%s",
                     decision.get("intent"), decision.get("confidence", 0.0),
                     {k: v for k, v in (decision.get("entities") or {}).items() if v})
        return {"intent": decision["intent"],
                "confidence": decision["confidence"],
                "category": nlu.get_intent_category(decision["intent"]),
                "method": "llm", "ood_reason": "",
                "llm_entities": decision["entities"],
                "llm_constraints": decision["constraints"]}

    # ------------------------------------------------------------------
    # persona (Alya / Raka) — nama & bahasa dari Setting
    # ------------------------------------------------------------------
    def _persona_ctx(self):
        """(persona_key, display_name) dari System Parameter. Tak pernah gagal."""
        try:
            Param = self.env["ir.config_parameter"].sudo()
            key = (Param.get_param("it_asset.ask_ai.persona", "alya") or "alya")
            configured = Param.get_param("it_asset.ask_ai.persona_name", "") or ""
        except Exception:
            key, configured = "alya", ""
        key = persona.normalize_persona((key or "").strip().lower())
        return key, persona.display_name(key, configured)

    @staticmethod
    def _social_reply(intent, text, lang, pkey, pname):
        """Jawaban sosial sadar persona: varian EN bila lang=='en',
        identity selalu memakai nama persona aktif."""
        if lang == "en" and intent in persona.SOCIAL_EN:
            tpl = persona.SOCIAL_EN[intent]
            return tpl.format(name=_esc(pname)) if "{name}" in tpl else tpl
        if intent == nlu.INTENT_IDENTITY:
            return persona.identity_text(pkey, pname, lang)
        return _canned_social(intent, text)

    def _ood_scope_out(self, text, reason):
        """Jawaban scope OOD bervariasi (dipakai 2 jalur: fast-path + F3b)."""
        out = self._out(text, nlu.INTENT_UNKNOWN, 0.9, "ood_scope_filter",
                        nlu.scope_reply(text, reason), "ood_scope_filter",
                        suggestions=self._suggest_for(nlu.INTENT_UNKNOWN))
        out["ood_reason"] = reason
        return out

    # ------------------------------------------------------------------
    # perakitan output + feedback (cermin CaptureReasonFor WACS)
    # ------------------------------------------------------------------
    def _merged_entities(self, text):
        """Extractor lokal + overlay LLM yang grounded (cermin WACS:
        ``entity.Extract`` pemilik kanal, ``Decision`` hanya constraints
        tertutup; nilai model diadopsi hanya bila         tertulis di pertanyaan)."""
        entities, constraints = nlu.extract_entities(text)
        # Ingatan topik sesi: "yang rusak?" setelah "stok cctv" = CCTV rusak.
        # Hanya bila pesan ini tanpa ref & tanpa kategori (topik baru menang).
        sctx = self.env.context.get("ask_ai_ctx") or {}
        if sctx and not entities.get("asset_refs") \
                and not entities.get("category"):
            for key in ("category", "asset_type", "radio_kind"):
                if not entities.get(key) and sctx.get(key):
                    entities[key] = sctx[key]
        llm = self.env.context.get("ask_ai_llm") or {}
        l_ent = llm.get("entities") or {}
        if not entities["asset_refs"] and (l_ent.get("asset_ref") or "").strip():
            v = l_ent["asset_ref"].strip().upper()
            # V2: bandingkan versi stripped agar "DT-02" tetap grounded
            # pada teks "dt 02" (tanpa menuntut format strip sama persis).
            try:
                v_stripped = nlu._stripped_ref(v)
                t_stripped = nlu._stripped_ref(text)
            except AttributeError:
                v_stripped, t_stripped = v, (text or "").upper()
            if v_stripped and v_stripped in t_stripped and any(ch.isdigit() for ch in v):
                _logger.info("Ask AI llm-entity adopted ref=%s", v)
                entities["asset_refs"] = [v]
        for key, slot in (("item", "item"), ("employee", "employee_name"),
                          ("category", "category")):
            if not entities.get(slot) and (l_ent.get(key) or "").strip():
                v = l_ent[key].strip()
                if v and v.lower() in (text or "").lower():
                    entities[slot] = v
        if (llm.get("constraints") or {}).get("low_only"):
            constraints["low_only"] = True
        return entities, constraints

    def _category_known(self, cat):
        """True bila kategori dikenal gazetteer/alias/kamus.

        Kamus kosong -> True (perilaku lama) agar kategori kustom di DB
        tetap jalan sebelum Generate pertama.
        """
        low = (cat or "").strip().lower()
        if not low:
            return True
        try:
            if low in [c.lower() for c in nlu.CATEGORY_GAZETTEER]:
                return True
        except Exception:
            pass
        try:
            if nlu.resolve_fleet_alias(cat):
                return True
        except Exception:
            pass
        try:
            Term = self.env["it_asset.ask_ai.term"]
            if not Term.search_count([]):
                return True
            return bool(Term.search(
                ["|", ("term", "ilike", cat), ("normalized", "ilike", cat)],
                limit=1))
        except Exception:
            return True

    def _kamus_suggest(self, cat, limit=3):
        """Saran istilah kamus mirip kategori tak dikenal (max `limit`)."""
        words = [w for w in (cat or "").split() if len(w) > 2]
        if not words:
            return []
        try:
            rows = self.env["it_asset.ask_ai.term"].search_read(
                [("term", "ilike", words[0])], ["term"], limit=limit)
        except Exception:
            rows = []
        return [r["term"] for r in rows if r.get("term")][:limit]

    def _validate_slots_db(self, text, intent, entities, original=None):
        """Level C (pola CALM): slot ada tapi tak cocok isi DB/kamus.

        Kembalikan out tanya-balik spesifik, atau None bila lolos.
        Hanya untuk intent berbasis ref/kategori; intent lain jalan seperti
        biasa agar perilaku lama tak berubah.

        F1: kode HANYA boleh disebut bila grounded di pertanyaan ASLI user
        (``original``). Bila tidak grounded — dari sumber mana pun — jangan
        pernah menuduh user mencari kode itu; fallback klarifikasi generik.
        """
        refs = entities.get("asset_refs") or []
        if intent in (nlu.INTENT_ASSET_DETAIL, nlu.INTENT_ASSET_USER,
                      nlu.INTENT_ASSET_HISTORY) and refs:
            kw = refs[0]
            if not cmds.is_grounded_code(kw, original or text):
                _logger.warning("Ask AI ungrounded-ref ditolak: %s", kw)
                return self._out(
                    text, intent, 0.0, "slot_repair",
                    nlu.clarification_reply(intent, entities),
                    "clarification",
                    suggestions=self._suggest_for(intent))
            try:
                found = self.env["it_asset.asset"].search_count(
                    self._asset_domain_for(kw)) or self.env["it_asset.unit"].search_count(
                        self._unit_domain_for(kw))
            except Exception:
                found = 1
            if not found:
                return self._out(
                    text, intent, 0.0, "slot_repair",
                    "Kode <b>%s</b> tidak ditemukan di data aset maupun unit. 🔍"
                    "<br/>Periksa kembali kodenya (mis. <i>ITLT-002</i>, "
                    "<i>PRN-01</i>, <i>DT-02</i>), atau ketik "
                    "<i>“rekap aset”</i>." % _esc(kw),
                    "clarification",
                    suggestions=["rekap aset", "stok radio ht", "bantuan"])
        if intent in (nlu.INTENT_ASSET_SEARCH, nlu.INTENT_CHECK_STOCK):
            cat = (entities.get("category") or "").strip()
            if cat and not self._category_known(cat):
                sug = self._kamus_suggest(cat)
                extra = ("<br/>Mungkin maksud Anda: %s"
                         % ", ".join("<i>%s</i>" % _esc(s) for s in sug)) if sug else ""
                return self._out(
                    text, intent, 0.0, "slot_repair",
                    "Kategori <b>%s</b> belum saya kenal. 🤔%s"
                    "<br/>Coba kategori umum seperti <i>laptop</i>, "
                    "<i>printer</i>, <i>radio</i>, atau ketik "
                    "<i>“rekap aset”</i>." % (_esc(cat), extra),
                    "clarification",
                    suggestions=(sug[:3] if sug else []) + ["rekap aset", "bantuan"])
        return None

    def _out(self, text, intent, conf, method, html, tool,
             action=None, handoff=False, suggestions=None):
        out = {"html": html, "intent": intent,
               "confidence": round(float(conf), 3), "method": method,
               "tool_executed": tool, "handoff": handoff}
        if action:
            out["action"] = action
        if suggestions:
            out["suggestions"] = list(suggestions)[:6]
        return out

    @staticmethod
    def _suggest_for(intent):
        """Tombol pilihan cepat sesuai topik (max 3-4)."""
        if intent == nlu.INTENT_CHECK_STOCK:
            return ["stok menipis", "stok radio ht", "rekap aset"]
        if intent in (nlu.INTENT_ASSET_DETAIL, nlu.INTENT_ASSET_USER,
                      nlu.INTENT_ASSET_HISTORY, nlu.INTENT_ASSET_TOP):
            return ["siapa pakai ITLT-007", "riwayat DT-02", "rekap aset"]
        if intent == nlu.INTENT_UNIT_DETAIL:
            return ["DT-02", "rekap aset", "bantuan"]
        if intent in (nlu.INTENT_HANDOVER_LIST, nlu.INTENT_REQUEST_STATUS,
                      nlu.INTENT_DAMAGE_LIST, nlu.INTENT_MAINTENANCE_LIST):
            return ["handover bulan ini", "rekap aset", "bantuan"]
        return ["rekap aset", "stok radio ht", "bantuan"]

    @staticmethod
    def _suggest_tags(rows, extra=None):
        out = []
        for a in rows[:4]:
            tag = a.get("asset_tag") or a.get("name") or ""
            if tag and tag not in out:
                out.append(tag)
        for s in extra or []:
            if s not in out:
                out.append(s)
        return out[:6]

    def _record_feedback(self, text, out):
        """Antrekan kurasi: hanya pesan tak-yakin (cermin WACS §17)."""
        reason, ok = nlu.capture_reason(out)
        if not ok:
            return
        try:
            Feedback = self.env["it_asset.ask_ai.feedback"].sudo()
            existing = Feedback.search(
                [("question_hash", "=", nlu.question_hash(text)),
                 ("status", "=", "new")], limit=1)
            vals = {"predicted_intent": out.get("intent", ""),
                    "confidence": out.get("confidence", 0.0),
                    "method": out.get("method", ""),
                    "tool_executed": out.get("tool_executed", ""),
                    "capture_reason": reason,
                    "ood_reason": out.get("ood_reason", ""),
                    "occurrences": 1,
                    "last_seen": fields.Datetime.now()}
            if existing:
                vals["occurrences"] = existing.occurrences + 1
                existing.write(vals)
            else:
                vals.update({"question": text[:500],
                             "question_hash": nlu.question_hash(text)})
                Feedback.create(vals)
        except Exception as exc:  # feedback tak boleh merusak jawaban
            _logger.warning("Ask AI feedback gagal direkam: %s", exc)

    # ------------------------------------------------------------------
    # riwayat chat (retensi otomatis 10 hari via cron, lihat ask_ai_history)
    # ------------------------------------------------------------------
    def _history_save(self, question, out, session_id=None):
        """Simpan pasangan pesan user+AI ke sesi milik user. Kembalikan id sesi."""
        text = (question or "").strip()
        if not text:
            return session_id
        Session = self.env["it_asset.ask_ai.session"]
        Message = self.env["it_asset.ask_ai.message"]
        session = None
        if session_id:
            session = Session.search(
                [("id", "=", session_id),
                 ("user_id", "=", self.env.user.id)], limit=1)
        if not session:
            session = Session.create({
                "name": text[:38] if len(text) > 0 else "Percakapan baru",
                "user_id": self.env.user.id,
            })
        elif session.name in (False, "", "Percakapan baru"):
            session.write({"name": text[:38]})
        Message.create({
            "session_id": session.id,
            "user_id": self.env.user.id,
            "role": "user",
            "body_html": _esc(text).replace("\n", "<br/>"),
        })
        Message.create({
            "session_id": session.id,
            "user_id": self.env.user.id,
            "role": "ai",
            "body_html": out.get("html") or "",
            "intent": out.get("intent") or "",
            "confidence": out.get("confidence") or 0.0,
        })
        # Lanjutan konfirmasi: ganti tiap hasil multi baru, bersihkan tiap
        # jawaban biasa (agar "semua" basi tak termakan nanti).
        pend_action = out.get("pending_action")
        pend_ids = out.get("pending_ids") or []
        if pend_action in _PENDING_ACTIONS and pend_ids:
            ids = []
            for i in pend_ids:
                try:
                    v = int(i)
                except (TypeError, ValueError):
                    continue
                if v not in ids:
                    ids.append(v)
            ids = ids[:_PENDING_CAP]
            if ids:
                session.write({
                    "pending_action": pend_action,
                    "pending_ids": ",".join(str(i) for i in ids),
                    "pending_label": (out.get("pending_label") or "")[:80],
                })
            elif session.pending_action:
                session.write({"pending_action": False,
                               "pending_ids": False,
                               "pending_label": False})
        elif session.pending_action:
            session.write({"pending_action": False,
                           "pending_ids": False,
                           "pending_label": False})
        # Flow engine: tulis / bersihkan state flow eksplisit. Stack
        # dipertahankan antar-jawaban biasa (untuk "lanjut/kembali"),
        # hanya flow_clear (batal/selesai) yang menghapusnya.
        if out.get("flow_clear"):
            session.write({"flow_name": False, "flow_step": False,
                           "flow_slots": False, "flow_stack": False})
        else:
            if "flow_stack" in out:
                try:
                    session.write({"flow_stack": out["flow_stack"] or []})
                except Exception as exc:
                    _logger.warning("Ask AI flow-stack gagal disimpan: %s", exc)
            if out.get("flow_name"):
                try:
                    session.write({
                        "flow_name": (out["flow_name"] or "")[:40],
                        "flow_step": (out.get("flow_step") or "")[:40],
                        "flow_slots": out.get("flow_slots") or {},
                    })
                except Exception as exc:
                    _logger.warning("Ask AI flow gagal disimpan: %s", exc)
            elif pend_action in _PENDING_ACTIONS and pend_ids:
                # bridge: tool masih mengembalikan pending_* lama ->
                # turunkan flow eksplisit agar pesan berikut lewat runner.
                fname, fstep = self._flow_from_pending(pend_action)
                if fname:
                    try:
                        session.write({
                            "flow_name": fname, "flow_step": fstep,
                            "flow_slots": {
                                "action": pend_action,
                                "ids": [int(i) for i in (out.get("pending_ids") or [])]
                                if isinstance(out.get("pending_ids"), list)
                                else self._pending_id_list(
                                    ",".join(str(i) for i in (out.get("pending_ids") or []))),
                                "label": (out.get("pending_label") or "")[:80],
                            },
                        })
                    except Exception as exc:
                        _logger.warning("Ask AI flow-bridge gagal: %s", exc)
            elif session.flow_name:
                session.write({"flow_name": False, "flow_step": False,
                               "flow_slots": False})
        # Ingatan topik: simpan kategori/domain terakhir (untuk pesan berikut).
        try:
            cur, _c = nlu.extract_entities(text)
        except Exception:
            cur = {}
        ctx_vals = {}
        for key, field in (("category", "last_category"),
                           ("asset_type", "last_asset_type"),
                           ("radio_kind", "last_radio_kind")):
            if cur.get(key):
                ctx_vals[field] = cur[key]
        if ctx_vals:
            ctx_vals["last_ctx_at"] = fields.Datetime.now()
            session.write(ctx_vals)
        session._touch()
        return session.id

    def _get_session_ctx(self, session_id):
        """Ingatan topik sesi (kedaluwarsa 60 menit) untuk _merged_entities."""
        if not session_id or not isinstance(session_id, int):
            return {}
        session = self.env["it_asset.ask_ai.session"].search(
            [("id", "=", session_id),
             ("user_id", "=", self.env.user.id)], limit=1)
        if not session or not session.last_ctx_at:
            return {}
        try:
            age = (_datetime.datetime.now() - session.last_ctx_at)
            age = age.total_seconds()
        except Exception:
            return {}
        if age > 3600:
            return {}
        return {k: v for k, v in {
            "category": session.last_category,
            "asset_type": session.last_asset_type,
            "radio_kind": session.last_radio_kind,
        }.items() if v}

    @api.model
    def session_list(self, limit=30):
        """Daftar sesi milik user (terbaru dulu) untuk sidebar."""
        rows = self.env["it_asset.ask_ai.session"].search_read(
            [("user_id", "=", self.env.user.id)],
            ["name", "last_seen", "message_count"],
            limit=min(max(int(limit or 30), 1), 100),
            order="last_seen desc, id desc")
        return rows

    @api.model
    def session_get(self, session_id):
        """Isi satu sesi (pesan berurutan) — hanya milik user."""
        session = self.env["it_asset.ask_ai.session"].search(
            [("id", "=", session_id),
             ("user_id", "=", self.env.user.id)], limit=1)
        if not session:
            return {"name": "", "messages": []}
        msgs = self.env["it_asset.ask_ai.message"].search_read(
            [("session_id", "=", session.id)],
            ["role", "body_html", "intent", "confidence", "create_date"],
            limit=200, order="id asc")
        return {"name": session.name, "messages": msgs}

    @api.model
    def session_create(self, name=None):
        session = self.env["it_asset.ask_ai.session"].create({
            "name": (name or "Percakapan baru")[:80],
            "user_id": self.env.user.id,
        })
        return {"id": session.id, "name": session.name}

    @api.model
    def session_rename(self, session_id, name):
        session = self.env["it_asset.ask_ai.session"].search(
            [("id", "=", session_id),
             ("user_id", "=", self.env.user.id)], limit=1)
        if session and (name or "").strip():
            session.write({"name": name.strip()[:80]})
        return bool(session)

    @api.model
    def session_delete(self, session_id):
        session = self.env["it_asset.ask_ai.session"].search(
            [("id", "=", session_id),
             ("user_id", "=", self.env.user.id)], limit=1)
        if session:
            session.unlink()
        return True

    # ------------------------------------------------------------------
    # konfirmasi lanjutan: "tag spesifik atau semua?"
    # ------------------------------------------------------------------
    def _pending_session(self, session_id):
        if not session_id or not isinstance(session_id, int):
            return None
        return self.env["it_asset.ask_ai.session"].search(
            [("id", "=", session_id),
             ("user_id", "=", self.env.user.id)], limit=1)

    @staticmethod
    def _pending_id_list(csv):
        ids = []
        for part in (csv or "").split(","):
            part = part.strip()
            if part.isdigit() and int(part) not in ids:
                ids.append(int(part))
        return ids[:_PENDING_CAP]

    # ------------------------------------------------------------------
    # flow engine (pola CALM): repair dulu, selebihnya delegasi ke pending
    # ------------------------------------------------------------------
    @staticmethod
    def _flow_from_pending(pending_action):
        if pending_action == nlu.INTENT_UNIT_DETAIL:
            return "unit_clarify", "await_info"
        if pending_action in _PENDING_ACTIONS:
            return "confirm_asset", "await_choice"
        return "", ""

    def _flow_state_from_session(self, session):
        """(flow_name, step, slots, stack) dari sesi (termasuk bridge legacy)."""
        flow_name = session.flow_name or ""
        step = session.flow_step or ""
        raw_slots = session.flow_slots
        slots = dict(raw_slots) if isinstance(raw_slots, dict) else {}
        raw_stack = session.flow_stack
        stack = list(raw_stack) if isinstance(raw_stack, list) else []
        if not flow_name and session.pending_action in _PENDING_ACTIONS:
            flow_name, step = self._flow_from_pending(session.pending_action)
            slots = {"action": session.pending_action,
                     "ids": self._pending_id_list(session.pending_ids),
                     "label": session.pending_label or ""}
        return flow_name, step, slots, stack

    @staticmethod
    def _flow_keys(flow_name, step, slots, stack=None):
        out = {"flow_name": flow_name, "flow_step": step,
               "flow_slots": slots or {}}
        if stack is not None:
            out["flow_stack"] = list(stack)
        return out

    def _match_candidate_ref(self, refs, ids):
        """True bila salah satu ref cocok kandidat ids (untuk confirm_asset)."""
        try:
            assets = self.env["it_asset.asset"].search_read(
                [("id", "in", ids)], ["asset_tag", "name", "lot_id"], limit=_PENDING_CAP)
        except Exception:
            return False
        blobs = []
        for a in assets:
            lot = a.get("lot_id")
            lot_name = lot[1] if isinstance(lot, (list, tuple)) else ""
            blobs.append(" ".join([
                nlu._stripped_ref(a.get("asset_tag") or ""),
                nlu._stripped_ref(a.get("name") or ""),
                nlu._stripped_ref(lot_name or "")]))
        for r in refs or []:
            key = nlu._stripped_ref(r)
            if key and any(key in b for b in blobs):
                return True
        return False

    def _push_flow(self, session, flow_name, step, slots, stack):
        """Simpan flow aktif ke stack (cap 3), bersihkan flow kini. None = lanjut pipeline."""
        entry = {"flow_name": flow_name, "step": step, "slots": slots or {}}
        new_stack = ([entry] + list(stack or []))[:3]
        try:
            session.write({"flow_name": False, "flow_step": False,
                           "flow_slots": False, "pending_action": False,
                           "pending_ids": False, "pending_label": False,
                           "flow_stack": new_stack})
        except Exception as exc:
            _logger.warning("Ask AI flow-push gagal: %s", exc)
        return None

    def _render_flow_question(self, text, flow_name, slots):
        """Gambar ulang pertanyaan flow (untuk resume). None bila tak bisa."""
        if flow_name == "confirm_asset":
            ids = slots.get("ids") or []
            try:
                assets = self.env["it_asset.asset"].search_read(
                    [("id", "in", ids)], _ASSET_FIELDS, order="id desc")
            except Exception:
                assets = []
            if not assets:
                return None
            action = slots.get("action") or nlu.INTENT_ASSET_DETAIL
            label = slots.get("label") or ""
            out = self._out(text, action, 1.0, "flow_resume",
                             self._confirm_html(action, label, assets[:8], len(assets)),
                             action,
                             suggestions=self._suggest_tags(assets, ["semua"]))
            out.update(self._flow_keys(flow_name, "await_choice", slots))
            return out
        if flow_name == "unit_clarify":
            ids = slots.get("ids") or []
            try:
                units = self.env["it_asset.unit"].search_read(
                    [("id", "in", ids)], _UNIT_FIELDS, order="name asc")
            except Exception:
                units = []
            if not units:
                return None
            unit = units[0]
            res = self._unit_clarify(unit)
            out = self._out(text, nlu.INTENT_UNIT_DETAIL, 1.0, "flow_resume",
                             res["html"], "unit_detail",
                             suggestions=res.get("suggestions"))
            out.update(self._flow_keys(flow_name, "await_info",
                                        {"ids": [u["id"] for u in units],
                                         "label": unit.get("name") or ""}))
            return out
        if flow_name == "guided_broken":
            prompt = cmds.FLOW_DEFS["guided_broken"]["prompts"]["await_category"]
            out = self._out(text, nlu.INTENT_ASSET_SEARCH, 1.0, "flow_resume",
                             prompt, "guided_broken",
                             suggestions=["laptop", "printer", "radio", "cctv"])
            out.update(self._flow_keys(flow_name, "await_category", slots))
            return out
        return None

    def _consume_flow(self, question, session_id):
        """Runner flow: repair (batal/ralat/lanjut) + new-topic push,
        selebihnya delegasi ke _consume_pending lama. None = bukan lanjutan."""
        session = self._pending_session(session_id)
        if not session:
            return None
        text = (question or "").strip()
        if not text:
            return None
        flow_name, step, slots, stack = self._flow_state_from_session(session)
        if not flow_name:
            if stack and cmds.detect_resume(text):
                top = stack[-1]
                rendered = self._render_flow_question(
                    text, top.get("flow_name", ""),
                    top.get("slots") or {})
                if rendered is not None:
                    rendered["flow_stack"] = list(stack[:-1])
                    return rendered
            return None
        # 1) repair: batal -> tutup flow (+ stack)
        if cmds.detect_cancel(text):
            out = self._out(
                text, nlu.INTENT_UNKNOWN, 0.0, "flow_cancel",
                "Baik, saya batalkan. 🙂 Ada lagi yang bisa saya bantu dari data IT?",
                "flow_close",
                suggestions=["rekap aset", "stok radio ht", "bantuan"])
            out["flow_clear"] = True
            return out
        # 2) repair: lanjutkan -> gambar ulang pertanyaan flow ini
        if cmds.detect_resume(text):
            rendered = self._render_flow_question(text, flow_name, slots)
            if rendered is not None:
                rendered["flow_stack"] = list(stack)
                return rendered
            return None
        # 3) guided_broken: kumpulkan kategori
        if flow_name == "guided_broken":
            return self._consume_guided_broken(text, slots)
        # 4) repair: koreksi -> coba sebagai jawaban flow
        correction = cmds.detect_correction(text)
        if correction:
            delegated = self._consume_pending(correction, session_id)
            if delegated is not None:
                delegated["method"] = "flow_correct"
                return delegated
            return self._push_flow(session, flow_name, step, slots, stack)
        # 5-6) petakan ke sinyal murni (sama dengan yang diuji di tests),
        # lalu putuskan: jawab flow -> delegasi pending lama; topik baru -> push.
        info = cmds.unit_info_transition(text) if flow_name == "unit_clarify" else ""
        ref_hit = False
        if flow_name in ("confirm_asset", "unit_clarify"):
            try:
                refs = nlu.extract_entities(text)[0].get("asset_refs") or []
            except Exception:
                refs = []
            if refs:
                if flow_name == "confirm_asset":
                    ref_hit = self._match_candidate_ref(refs, slots.get("ids") or [])
                else:
                    ref_hit = self._match_unit_ref(refs, slots.get("ids") or [])
        signal = cmds.flow_signal(flow_name, {"step": step, "slots": slots},
                                  text, ref_hit=ref_hit, info=info)
        if signal in ("show_all", "show_one", "info_brand", "info_status",
                      "info_assets", "info_history"):
            return self._consume_pending(text, session_id)
        if signal == "new_topic":
            return self._push_flow(session, flow_name, step, slots, stack)
        return None

    def _match_unit_ref(self, refs, ids):
        """True bila salah satu ref cocok nama unit kandidat."""
        try:
            units = self.env["it_asset.unit"].search_read(
                [("id", "in", ids)], ["name"], limit=_PENDING_CAP)
        except Exception:
            return False
        variants = {nlu._stripped_ref(u.get("name") or "") for u in units}
        variants.discard("")
        for r in refs or []:
            if nlu._stripped_ref(r) in variants:
                return True
        return False

    def _consume_guided_broken(self, text, slots):
        """Kumpulkan kategori untuk guided_broken, lalu eksekusi asset_search."""
        correction = cmds.detect_correction(text)
        if correction:
            text = correction
        try:
            eff = self._dict_assist(text).get("effective_text") or text
        except Exception:
            eff = text
        try:
            entities = nlu.extract_entities(eff)[0]
        except Exception:
            entities = {}
        cat = (entities.get("category") or nlu.resolve_fleet_alias(eff)
               or entities.get("item") or "").strip()
        if not cat or len(cat) < 3:
            prompt = cmds.FLOW_DEFS["guided_broken"]["prompts"]["await_category"]
            out = self._out(text, nlu.INTENT_ASSET_SEARCH, 0.0, "flow_ask",
                             "Saya belum menangkap kategorinya. 🙂<br/>" + prompt,
                             "clarification",
                             suggestions=["laptop", "printer", "radio", "cctv"])
            out.update(self._flow_keys("guided_broken", "await_category", slots or {}))
            return out
        handler = self._TOOLS.get(nlu.INTENT_ASSET_SEARCH)
        try:
            result = handler(self, "rusak " + cat)
        except Exception as exc:
            _logger.warning("Ask AI guided-broken gagal: %s", exc)
            result = None
        if not isinstance(result, dict) or result.get("miss"):
            out = self._out(text, nlu.INTENT_ASSET_SEARCH, 0.95, "flow_execute",
                             nlu.data_miss_reply(text) + ((result or {}).get("hint") or ""),
                             "data_miss",
                             suggestions=["rekap aset", "aset rusak", "bantuan"])
            out["flow_clear"] = True
            return out
        out = self._out(text, nlu.INTENT_ASSET_SEARCH, 0.95, "flow_execute",
                         result["html"], result.get("tool", "asset_search"),
                         action=result.get("action"),
                         suggestions=result.get("suggestions"))
        try:
            polished = self._maybe_rephrase(out.get("html") or "",
                                            nlu.INTENT_ASSET_SEARCH,
                                            persona.detect_language(text))
            if polished:
                out["html"] = polished
                out["rephrased"] = True
        except Exception as exc:
            _logger.warning("Ask AI rephrase gagal, pakai jawaban asli: %s", exc)
        out["flow_clear"] = True
        out["grounding"] = {"flow": "guided_broken", "slots": {"category": cat}}
        return out

    def _consume_pending(self, question, session_id):
        """Selesaikan pertanyaan konfirmasi tertunda. None = bukan lanjutan."""
        session = self._pending_session(session_id)
        if not session or not session.pending_action:
            return None
        if session.pending_action not in _PENDING_ACTIONS:
            return None
        ids = self._pending_id_list(session.pending_ids)
        if not ids:
            return None
        text = (question or "").strip()
        if not text:
            return None
        if session.pending_action == nlu.INTENT_UNIT_DETAIL:
            return self._consume_pending_unit(text, session, ids)
        assets = self.env["it_asset.asset"].search_read(
            [("id", "in", ids)], _ASSET_FIELDS, order="id desc")
        if not assets:
            return None
        # (a) "semua" / "ya" -> tampilkan semua kandidat
        if len(text) <= 40 and _RE_SHOW_ALL.match(text):
            return self._render_pending_all(
                text, session.pending_action, assets)
        # (b) tag/SN spesifik yang cocok salah satu kandidat
        entities, _c = self._merged_entities(text)
        refs = entities.get("asset_refs") or []
        if refs:
            def _blob(a):
                lot = a.get("lot_id")
                lot_name = lot[1] if isinstance(lot, (list, tuple)) else ""
                return " ".join([
                    nlu._stripped_ref(a.get("asset_tag") or ""),
                    nlu._stripped_ref(a.get("name") or ""),
                    nlu._stripped_ref(lot_name or "")])
            blobs = [(a, _blob(a)) for a in assets]
            hit = None
            for r in refs:
                key = nlu._stripped_ref(r)
                if not key:
                    continue
                for a, blob in blobs:
                    if key in blob:
                        hit = a
                        break
                if hit is not None:
                    break
            if hit is not None:
                handler = self._TOOLS.get(session.pending_action)
                if handler:
                    tag = hit.get("asset_tag") or hit.get("name") or ""
                    try:
                        result = handler(self, tag)
                    except Exception as exc:
                        _logger.warning("Ask AI pending-confirm gagal: %s", exc)
                        return None
                    if isinstance(result, dict) and not result.get("miss"):
                        out = self._out(
                            text, session.pending_action, 1.0,
                            "pending_confirm", result["html"],
                            result.get("tool", session.pending_action),
                            action=result.get("action"))
                        for k in ("pending_action", "pending_ids",
                                  "pending_label"):
                            if k in result:
                                out[k] = result[k]
                        return out
                    return None
        return None

    def _consume_pending_unit(self, text, session, ids):
        """Lanjutan unit: 'semua' -> kartu; kata info -> jawaban fokus."""
        units = self.env["it_asset.unit"].search_read(
            [("id", "in", ids)], _UNIT_FIELDS, order="name asc")
        if not units:
            return None
        if len(text) <= 40 and _RE_SHOW_ALL.match(text):
            cards = "".join(
                self._unit_card(u, self._installed_assets(u["id"]))
                for u in units[:3])
            if len(units) > 3:
                cards += ("<div class='ai-foot'>+%d unit lain — balas kode "
                          "unit spesifiknya.</div>" % (len(units) - 3))
            return self._out(text, nlu.INTENT_UNIT_DETAIL, 1.0,
                             "pending_confirm", cards, "unit_detail")
        unit = units[0]
        assets = self._installed_assets(unit["id"])
        low = text.lower()
        keep = {"pending_action": nlu.INTENT_UNIT_DETAIL,
                "pending_ids": [u["id"] for u in units],
                "pending_label": session.pending_label or ""}
        if any(w in low for w in ("merek", "merk", "brand", "model")):
            out = self._out(
                text, nlu.INTENT_UNIT_DETAIL, 1.0, "pending_confirm",
                "🏷️ Unit <b>%s</b> — Merek: <b>%s</b>, Model: <b>%s</b> "
                "(%s)." % (_esc(unit.get("name") or "-"),
                           _esc(unit.get("brand") or "-"),
                           _esc(unit.get("model") or "-"),
                           _esc(_m2o(unit.get("category_id")) or "-")),
                "unit_detail")
            out.update(keep)
            return out
        if any(w in low for w in ("status", "kondisi")):
            out = self._out(
                text, nlu.INTENT_UNIT_DETAIL, 1.0, "pending_confirm",
                "🚜 Unit <b>%s</b> berstatus <b>%s</b> (%s) — %d aset "
                "terpasang." % (
                    _esc(unit.get("name") or "-"),
                    _esc(_UNIT_STATE_MAP.get(unit.get("state"),
                                             unit.get("state") or "-")),
                    _esc(_m2o(unit.get("category_id")) or "-"), len(assets)),
                "unit_detail")
            out.update(keep)
            return out
        if any(w in low for w in ("aset", "radio", "terpasang", "pasang")):
            if assets:
                html = self._asset_table(
                    "📻 Aset terpasang di <b>%s</b> (%d):" % (
                        _esc(unit.get("name") or "-"), len(assets)), assets)
            else:
                html = ("Belum ada aset terpasang di unit <b>%s</b>."
                        % _esc(unit.get("name") or "-"))
            out = self._out(text, nlu.INTENT_UNIT_DETAIL, 1.0,
                            "pending_confirm", html, "unit_detail")
            out.update(keep)
            return out
        if any(w in low for w in ("riwayat", "sejarah", "history")):
            out = self._out(text, nlu.INTENT_UNIT_DETAIL, 1.0,
                            "pending_confirm",
                            self._render_unit_history(unit, assets),
                            "unit_detail")
            out.update(keep)
            return out
        # kode unit disebut lagi -> kartu lengkap
        try:
            variants = set()
            for u in units:
                variants.add(nlu._stripped_ref(u.get("name") or ""))
            refs = (self._merged_entities(text)[0].get("asset_refs") or [])
            if any(nlu._stripped_ref(r) in variants for r in refs):
                return self._out(
                    text, nlu.INTENT_UNIT_DETAIL, 1.0, "pending_confirm",
                    self._unit_card(unit, assets), "unit_detail")
        except Exception as exc:
            _logger.warning("Ask AI pending-unit gagal: %s", exc)
        return None

    def _render_unit_history(self, unit, assets):
        parts = ["📜 <b>Riwayat unit %s:</b>"
                 % _esc(unit.get("name") or "-")]
        swaps = self.env["it_asset.swap"].search_read(
            [("unit_id", "=", unit["id"])],
            ["asset_id", "assignment_date", "return_date", "notes", "state"],
            limit=10, order="assignment_date desc")
        if swaps:
            parts.append("<div class='ai-sec'>🔄 <b>Pasang/lepas (%d):</b></div>"
                         "<div class='ai-timeline'>" % len(swaps))
            for s in swaps:
                parts.append(
                    "<div class='ai-tl-row'><i class='fa fa-refresh'></i><div><b>%s</b>"
                    "<div class='ai-sub'>%s → %s • %s</div></div></div>" % (
                        _esc(_m2o(s.get("asset_id")) or "-"),
                        _esc(s.get("assignment_date") or "-"),
                        _esc(s.get("return_date") or "sekarang"),
                        _esc((s.get("notes") or "")[:80])))
            parts.append("</div>")
        else:
            parts.append("<div class='ai-sub'>Belum ada riwayat pasang/lepas.</div>")
        aids = [a["id"] for a in assets]
        if aids:
            maints = self.env["it_asset.maintenance"].search_read(
                [("asset_id", "in", aids)], _MAINT_FIELDS + ["create_date"],
                limit=10, order="maintenance_date desc")
            if maints:
                parts.append("<div class='ai-sec'>🔧 <b>Maintenance aset "
                             "terpasang (%d):</b></div>" % len(maints))
                for m in maints[:5]:
                    parts.append(
                        "<div class='ai-tl-row'><i class='fa fa-wrench'></i><div>"
                        "<b>%s — %s</b><div class='ai-sub'>%s</div></div></div>"
                        % (_esc(m.get("maintenance_date") or "-"),
                           _esc(_m2o(m.get("asset_id")) or "-"),
                           _esc((m.get("description") or "-")[:80])))
        return "".join(parts)

    def _render_pending_all(self, text, action, assets):
        if action == nlu.INTENT_ASSET_HISTORY:
            html = self._render_history_all(assets)
        elif action == nlu.INTENT_ASSET_USER:
            html = self._asset_table(
                "👤 Pengguna <b>%d</b> aset:" % len(assets), assets[:15])
        else:
            html = self._asset_table(
                "🔎 <b>%d</b> aset:" % len(assets), assets[:15])
        if len(assets) > 15:
            html += ("<div class='ai-foot'>Menampilkan 15 dari %d. Balas "
                     "tag spesifik untuk fokus ke satu aset.</div>"
                     % len(assets))
        return self._out(text, action, 1.0, "pending_confirm", html, action,
                         action=self._list_action(
                             "Assets", "it_asset.asset",
                             [("id", "in", [a["id"] for a in assets[:15]])]))

    def _render_history_all(self, assets):
        Assign = self.env["it_asset.assignment"]
        parts = ["📜 <b>Riwayat %d aset:</b>" % len(assets)]
        for a in assets[:5]:
            tag = a.get("asset_tag") or a.get("name") or "-"
            rows = Assign.search_read(
                [("asset_id", "=", a["id"])], _ASSIGN_FIELDS, limit=5,
                order="assignment_date desc")
            if not rows:
                parts.append("<div class='ai-sec'><b>%s</b> — belum ada "
                             "riwayat.</div>" % _esc(tag))
                continue
            parts.append("<div class='ai-sec'><b>%s — %s</b> (%d):</div>"
                         "<div class='ai-timeline'>" % (
                             _esc(tag), _esc(a.get("name") or "-"), len(rows)))
            for s in rows:
                parts.append(
                    "<div class='ai-tl-row'><i class='fa fa-user'></i><div><b>%s</b>"
                    "<div class='ai-sub'>%s → %s • %s</div></div></div>" % (
                        _esc(_m2o(s.get("employee_id")) or "-"),
                        _esc(s.get("assignment_date") or "-"),
                        _esc(s.get("return_date") or "sekarang"),
                        _esc(s.get("state") or "-")))
            parts.append("</div>")
        if len(assets) > 5:
            parts.append("<div class='ai-foot'>+%d aset lain — balas tag "
                         "spesifiknya untuk riwayat penuh.</div>"
                         % (len(assets) - 5))
        return "".join(parts)

    def _confirm_html(self, action, kw_label, rows, total):
        lines = []
        for a in rows[:8]:
            lines.append("<div>🏷️ <b>%s</b> — %s</div>" % (
                _esc(a.get("asset_tag") or "-"),
                _esc(a.get("name") or "-")))
        extra = ""
        if total > len(rows[:8]):
            extra = ("<div>+ %d lainnya — persempit atau ketik "
                     "<i>“semua”</i>.</div>" % (total - len(rows[:8])))
        verb = {nlu.INTENT_ASSET_USER: "penggunanya",
                nlu.INTENT_ASSET_DETAIL: "detailnya",
                nlu.INTENT_ASSET_HISTORY: "riwayatnya",
                nlu.INTENT_UNIT_DETAIL: "informasinya"}.get(action, "datanya")
        openings = [
            "Ditemukan <b>%d</b> aset mirip “<b>%s</b>”. Mau lihat %s yang mana?",
            "Ada <b>%d</b> kandidat untuk “<b>%s</b>” — %s yang mana?",
            "Ada <b>%d</b> aset cocok “<b>%s</b>”. Pilih %s yang mana?",
        ]
        opening = openings[nlu._pick_variant(
            "%s|%s" % (action, kw_label), len(openings))]
        opening = opening % (total, _esc(kw_label), verb)
        tail = ("<div class='ai-help'>%s</div>%s"
                "Balas dengan <b>nomor aset / SN</b>-nya, atau ketik "
                "<i>“semua”</i>." % ("".join(lines), extra))
        return opening + tail

    # ------------------------------------------------------------------
    # helpers HTML (grounded: semua nilai dari evidence ORM, di-escape)
    # ------------------------------------------------------------------
    @staticmethod
    def _state_badge(state):
        cls = {"available": "ok", "in_use": "info",
               "maintenance": "warn", "retired": "muted"}.get(state, "muted")
        return "<span class='ai-badge %s'>%s</span>" % (
            cls, _esc(_STATE_LABEL.get(state, state or "-")))

    @staticmethod
    def _cond_badge(cond):
        cls = {"good": "ok", "degraded": "warn",
               "broken": "bad"}.get(cond, "muted")
        return "<span class='ai-badge %s'>%s</span>" % (
            cls, _esc(_COND_LABEL.get(cond, cond or "-")))

    def _asset_table(self, title, rows):
        parts = [title, "<div class='ai-table-wrap'><table class='ai-table'>"
                        "<thead><tr><th>Aset</th><th>Pengguna</th>"
                        "<th>Status</th></tr></thead><tbody>"]
        for a in rows:
            # Lencana domain dari DB (asset_type), bukan tebakan kode —
            # biar jelas ini aset IT atau Operasional.
            dom = a.get("asset_type")
            if dom == "it":
                dom_badge = "<span class='ai-tag'>IT</span> "
            elif dom == "operation":
                dom_badge = "<span class='ai-tag ops'>OPS</span> "
            else:
                dom_badge = ""
            tag = (dom_badge + "<span class='ai-tag'>%s</span> " % _esc(a["asset_tag"])
                   if a.get("asset_tag") else dom_badge)
            serial = ("<div class='ai-sub'>SN: %s</div>" % _esc(_m2o(a.get("lot_id")))
                      if a.get("lot_id") else "")
            cat = ("<div class='ai-sub'>%s</div>" % _esc(_m2o(a.get("category_id")))
                   if a.get("category_id") else "")
            if a.get("employee_id"):
                user = _esc(_m2o(a["employee_id"]))
            elif a.get("unit_id"):
                user = "Unit: " + _esc(_m2o(a["unit_id"]))
            else:
                user = "<span class='ai-sub'>—</span>"
            parts.append(
                "<tr><td>%s<b>%s</b>%s%s</td><td>%s</td><td>%s"
                "<div style='margin-top:4px'>%s</div></td></tr>" % (
                    tag, _esc(a.get("name") or "-"), serial, cat, user,
                    self._state_badge(a.get("state")),
                    self._cond_badge(a.get("condition"))))
        parts.append("</tbody></table></div>")
        return "".join(parts)

    @staticmethod
    def _list_action(label, model, domain):
        return {"res_model": model, "domain": domain, "name": label}

    # ------------------------------------------------------------------
    # unit/fleet detail (goals2.md §13)
    # ------------------------------------------------------------------
    def _is_bare_unit_query(self, text, kw):
        """True bila teks hanya kode unit + kata umum (tanpa info spesifik).

        'dt 02' -> True (tanya balik); 'dt 02.07 itu merek apa' -> False.
        """
        low = (text or "").lower()
        if any(w in low for w in _SPECIFIC_UNIT_WORDS):
            return False
        try:
            variants = nlu.ref_variants(kw)
        except AttributeError:
            variants = [kw]
        s = low
        for v in sorted(variants, key=len, reverse=True):
            if v:
                s = s.replace(v.lower(), " ")
        toks = [t for t in _re.split(r"[^a-z0-9]+", s) if t]
        return all(t in _GENERIC_UNIT_WORDS or len(t) < 2 or t.isdigit()
                   for t in toks)

    def _installed_assets(self, unit_id, limit=15):
        return self.env["it_asset.asset"].search_read(
            [("unit_id", "=", unit_id)], _ASSET_FIELDS, limit=limit,
            order="id desc")

    def _unit_card(self, unit, assets, foot=""):
        badge = ("<span class='ai-badge info'>%s</span>"
                 % _esc(_UNIT_STATE_MAP.get(unit.get("state"),
                                            unit.get("state") or "-")))
        parts = [
            "<div class='ai-detail-head'>",
            ("<span class='ai-tag big'>%s</span>" % _esc(unit["name"])
             if unit.get("name") else ""),
            "<b>%s %s</b></div>"
            "<div class='ai-sub'>Aset operasional (fleet) — bukan IT</div>" % (
                _esc(_m2o(unit.get("category_id")) or "Unit"),
                _esc(unit.get("name") or "")),
            "<div class='ai-kv'>"
            "<div><span>Merek</span><b>%s</b></div>"
            "<div><span>Model</span><b>%s</b></div>"
            "<div><span>Kategori</span><b>%s</b></div>"
            "<div><span>Status</span>%s</div>"
            "</div>" % (_esc(unit.get("brand") or "-"),
                        _esc(unit.get("model") or "-"),
                        _esc(_m2o(unit.get("category_id")) or "-"),
                        badge),
        ]
        if unit.get("remarks"):
            parts.append("<div class='ai-sub'>Keterangan: %s</div>"
                         % _esc(unit["remarks"]))
        if assets:
            parts.append(self._asset_table(
                "📻 Aset terpasang (%d):" % len(assets), assets))
        else:
            parts.append("<div class='ai-sub'>Belum ada aset terpasang "
                         "di unit ini.</div>")
        if foot:
            parts.append(foot)
        else:
            parts.append(
                "<div class='ai-foot'>Tanya spesifik: "
                "<i>“merek %s?”</i> • <i>“status %s?”</i> • "
                "<i>“radio di %s?”</i></div>" % (
                    _esc(unit.get("name") or ""), _esc(unit.get("name") or ""),
                    _esc(unit.get("name") or "")))
        return "".join(parts)

    def _unit_clarify(self, unit):
        """Kode polos ('dt 02') -> tanya balik mau info apa + pending."""
        name = unit.get("name") or "-"
        openings = [
            "🚜 Unit <b>%s</b> (%s) — mau tau informasi apa?<br/>",
            "Siap, unit <b>%s</b> ketemu (%s). Mau info yang mana?<br/>",
        ]
        opening = openings[nlu._pick_variant("unit_clarify|%s" % name,
                                             len(openings))]
        return {"html":
                (opening + "<div class='ai-help'>"
                 "<div>🏷️ <b>Merek &amp; model</b> — balas <i>“merek”</i></div>"
                 "<div>🚦 <b>Status</b> — balas <i>“status”</i></div>"
                 "<div>📻 <b>Aset terpasang</b> — balas <i>“aset”</i> / <i>“radio”</i></div>"
                 "<div>📜 <b>Riwayat</b> — balas <i>“riwayat”</i></div>"
                "</div>Balas salah satunya, atau ketik <i>“semua”</i> untuk "
                "kartu lengkap.") % (
                    _esc(name),
                    _esc(_m2o(unit.get("category_id")) or "-")),
                "tool": "unit_detail",
                "pending_action": nlu.INTENT_UNIT_DETAIL,
                "pending_ids": [unit["id"]],
                "pending_label": unit.get("name") or "",
                "suggestions": ["merek", "status", "aset", "riwayat",
                                "semua"]}

    def _tool_unit_detail(self, text):
        entities, _c = self._merged_entities(text)
        refs = entities.get("asset_refs") or []
        fleet_refs = [r for r in refs if nlu._is_fleet_ref(r)]
        U = self.env["it_asset.unit"]
        if fleet_refs:
            kw = fleet_refs[0]
            units = U.search_read(self._unit_domain_for(kw), _UNIT_FIELDS,
                                  limit=5, order="name asc")
            if not units:
                return {"miss": True,
                        "hint": "<div class='ai-foot'>Periksa kode unitnya "
                                "(mis. <i>DT-02</i>, <i>EX-05</i>, "
                                "<i>LV-02</i>).</div>"}
            if len(units) > 1:
                ids = [u["id"] for u in units]
                lines = "".join(
                    "<div>🚜 <b>%s</b> — %s</div>" % (
                        _esc(u.get("name") or "-"),
                        _esc(_m2o(u.get("category_id")) or "-"))
                    for u in units)
                return {"html":
                        "Ditemukan <b>%d</b> unit mirip “<b>%s</b>”:"
                        "<div class='ai-help'>%s</div>"
                        "Balas <b>kode unit</b> yang dimaksud, atau ketik "
                        "<i>“semua”</i>." % (
                            len(units), _esc(kw), lines),
                        "tool": "unit_detail",
                        "pending_action": nlu.INTENT_UNIT_DETAIL,
                        "pending_ids": ids,
                        "pending_label": kw,
                        "suggestions": [u.get("name") or "" for u in units[:4]
                                        if u.get("name")] + ["semua"]}
            unit = units[0]
            assets = self._installed_assets(unit["id"])
            if self._is_bare_unit_query(text, kw):
                return self._unit_clarify(unit)
            return {"html": self._unit_card(unit, assets),
                    "tool": "unit_detail",
                    "action": self._list_action(
                        unit.get("name") or "Unit", "it_asset.unit",
                        [("id", "=", unit["id"])])}
        if refs:
            # ref non-fleet (ITLT-007, PRN-01) -> alur detail aset biasa
            return self._tool_asset_detail(text)
        cat = (entities.get("category") or "")
        if cat in _FLEET_CATEGORIES or nlu.resolve_fleet_alias(text):
            return self._tool_unit_search(text, entities)
        return None

    # ------------------------------------------------------------------
    # TOOLS — tiap tool: entities → evidence ORM → html (+ action opsional)
    # Kembalikan None bila entitas kurang (→ klarifikasi), atau
    # {"miss": True, "hint": ...} bila data tak ditemukan.
    # ------------------------------------------------------------------
    def _tool_recap(self, _text):
        A = self.env["it_asset.asset"]
        total = A.search_count([])
        it = A.search_count([("asset_type", "=", "it")])
        op = A.search_count([("asset_type", "=", "operation")])
        avail = A.search_count([("state", "=", "available")])
        in_use = A.search_count([("state", "=", "in_use")])
        maint = A.search_count([("state", "=", "maintenance")])
        retired = A.search_count([("state", "=", "retired")])
        good = A.search_count([("condition", "=", "good")])
        degraded = A.search_count([("condition", "=", "degraded")])
        broken = A.search_count([("condition", "=", "broken")])
        try:
            cons = self.env["it_asset.consumable"].search_read(
                [], ["qty_available", "min_quantity"], limit=500)
            low = sum(1 for c in cons
                      if (c["qty_available"] or 0) <= (c["min_quantity"] or 0))
        except Exception:
            low = 0
        html = (
            "📊 <b>Rekap aset &amp; stok saat ini</b> (data live):"
            "<div class='ai-kpi'>"
            "<div class='ai-kpi-item'><span>Total Aset</span><b>%d</b>"
            "<small>IT %d • Operasional %d</small></div>"
            "<div class='ai-kpi-item green'><span>Tersedia</span><b>%d</b>"
            "<small>siap dipakai</small></div>"
            "<div class='ai-kpi-item blue'><span>Dipakai</span><b>%d</b>"
            "<small>ter-assign</small></div>"
            "<div class='ai-kpi-item red'><span>Out of Service</span><b>%d</b>"
            "<small>retired: %d</small></div>"
            "<div class='ai-kpi-item'><span>Kondisi Broken</span><b>%d</b>"
            "<small>degraded: %d • good: %d</small></div>"
            "<div class='ai-kpi-item amber'><span>Stok Menipis</span><b>%d</b>"
            "<small>consumable ≤ min</small></div>"
            "</div>"
            "<div class='ai-foot'>Tanya lebih detail: <i>“aset broken apa "
            "saja?”</i> • <i>“laptop tersedia”</i> • <i>“stok menipis”</i></div>"
            % (total, it, op, avail, in_use, maint, retired,
               broken, degraded, good, low))
        return {"html": html, "tool": "recap"}

    def _consumable_search(self, kw):
        """Cari consumable: frasa dulu, lalu AND-kata, lalu OR-kata.

        Presisi dulu (biar 'adaptor bnc' tak langsung miss), recall
        belakangan (biar tetap ketemu walau kata tak berurutan).
        """
        C = self.env["it_asset.consumable"]
        phrase = (kw or "").strip()
        if not phrase:
            return []
        tried = [["|", ("name", "ilike", phrase),
                  ("product_id.name", "ilike", phrase)]]
        words = [w for w in phrase.split() if len(w) > 1]
        if len(words) > 1:
            ands = []
            for w in words:
                ands.extend(["|", ("name", "ilike", w),
                             ("product_id.name", "ilike", w)])
            tried.append(ands)
            ors = []
            for w in words:
                ors.extend([("name", "ilike", w),
                            ("product_id.name", "ilike", w)])
            tried.append(_or_domain(ors))
        for dom in tried:
            rows = C.search_read(dom, _CONSUMABLE_FIELDS, limit=20,
                                 order="name asc")
            if rows:
                return rows
        return []

    # Kata generik kategori/jenis: bila item masih punya kata produk lain
    # ("mic" pada "mic radio"), itu barang spesifik -> consumable dulu.
    _CATEGORY_NOISE_WORDS = frozenset([
        "radio", "rig", "ht", "handy", "talky", "laptop", "printer",
        "komputer", "monitor", "mouse", "keyboard", "server", "cctv",
        "it", "operasional",
    ])

    @staticmethod
    def _is_generic_asset_stock(entities, kw):
        """True bila 'stok X' murni menanyakan kategori aset (tanpa kata produk)."""
        cat = (entities.get("category") or "").lower()
        if cat not in ITAskAI._ASSET_STOCK_CATEGORIES:
            return False
        catwords = set(cat.split())
        for tok in (kw or "").lower().split():
            if len(tok) < 2:
                continue
            if tok in catwords or tok in ITAskAI._CATEGORY_NOISE_WORDS:
                continue
            return False
        return True

    def _tool_check_stock(self, text):
        entities, constraints = self._merged_entities(text)
        kw = entities["item"]
        C = self.env["it_asset.consumable"]
        if kw:
            # Barang berupa aset (radio/laptop/...) -> stok = unit tersedia,
            # JANGAN consumable ("mic radio rig" bukan "radio rig").
            # Tapi kalau ada kata produk spesifik ("mic"), consumable dulu.
            if self._is_generic_asset_stock(entities, kw):
                res = self._tool_asset_stock(text, entities, kw)
                if not res.get("miss"):
                    return res
            rows = self._consumable_search(kw)
            if rows:
                return {"html": self._stock_table(
                    "Stok untuk “<b>%s</b>” (%d item):" % (_esc(kw), len(rows)),
                    rows), "tool": "check_stock",
                    "action": self._list_action(
                        "Consumables", "it_asset.consumable",
                        ["|", ("name", "ilike", kw),
                         ("product_id.name", "ilike", kw)])}
            if self._is_generic_asset_stock(entities, kw):
                return {"miss": True,
                        "hint": "<div class='ai-foot'>Tidak ada stok maupun "
                                "aset “<b>%s</b>” tercatat. Coba kata lain "
                                "atau ketik <i>“rekap aset”</i>.</div>"
                        % _esc(kw)}
            # Bukan kategori aset -> fallback lama (keyword ke data aset).
            return self._tool_asset_stock(text, entities, kw)
        # Tanpa keyword tapi ada topik sesi ("stok nya sisa?" setelah bahas
        # HT) -> stok kategori itu, bukan ringkasan umum. low_only tetap umum.
        if not constraints["low_only"]:
            cat = (entities.get("category") or "")
            kind = (entities.get("radio_kind") or "")
            if cat in self._ASSET_STOCK_CATEGORIES or kind:
                label = (cat + (" " + kind if kind and kind not in
                                cat.lower() else "")).strip() or "aset"
                return self._tool_asset_stock(text, entities, label or kw)
        rows = C.search_read([], _CONSUMABLE_FIELDS, limit=500,
                             order="name asc")
        if not rows:
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Tambahkan dulu di menu "
                            "<b>Inventory → IT → Consumables</b>.</div>"}
        if constraints["low_only"]:
            low = [c for c in rows
                   if (c["qty_available"] or 0) <= (c["min_quantity"] or 0)]
            if not low:
                return {"html":
                        "Semua stok aman ✅ — tidak ada consumable di bawah "
                        "minimum (total %d item terpantau)." % len(rows),
                        "tool": "check_stock"}
            return {"html": self._stock_table(
                "⚠️ <b>%d</b> consumable perlu restock (sisa ≤ minimum):"
                % len(low), low), "tool": "check_stock"}
        total_qty = sum(c["qty_available"] or 0 for c in rows)
        low_n = sum(1 for c in rows
                    if (c["qty_available"] or 0) <= (c["min_quantity"] or 0))
        out_n = sum(1 for c in rows if (c["qty_available"] or 0) <= 0)
        return {"html": self._stock_table(
            "📦 <b>Ringkasan stok</b> — %d item • total %s pcs • %d menipis • "
            "%d habis.<br/>Menampilkan 15 pertama:" % (
                len(rows), total_qty, low_n, out_n), rows[:15],
            "Ketik <i>“stok menipis”</i> untuk daftar restock atau "
            "<i>“stok [nama barang]”</i> untuk cek spesifik."),
            "tool": "check_stock"}

    # Stok versi aset: "berapa stok radio ht" = HT yang tersedia/belum assign.
    _ASSET_STOCK_CATEGORIES = frozenset([
        "laptop", "desktop", "printer", "radio", "rig", "radio rig",
        "radio ht", "ht", "handy talky", "monitor", "mouse", "keyboard",
        "pc", "komputer", "server", "router", "switch", "cctv", "gps",
        "tablet", "headset", "proyektor", "projector",
    ])

    def _tool_asset_stock(self, text, entities, kw):
        """Jawaban stok untuk barang berupa aset: yang tersedia/belum assign."""
        A = self.env["it_asset.asset"]
        cat = (entities.get("category") or "").lower()
        base = []
        if cat in self._ASSET_STOCK_CATEGORIES:
            base.append(("category_id.name", "ilike", entities["category"]))
        kind = (entities.get("radio_kind") or "")
        kind_domain = []
        if kind == "rig":
            kind_domain = ["|", ("name", "ilike", "rig"),
                           ("product_id.name", "ilike", "rig")]
        elif kind == "ht":
            kind_domain = ["|", ("name", "ilike", "HT"),
                           ("product_id.name", "ilike", "HT")]
        if not base:
            base = self._asset_domain_for(kw)
        if kind_domain and A.search_count(base + kind_domain):
            base = base + kind_domain
        avail_dom = base + [("state", "=", "available")]
        inuse_dom = base + [("state", "=", "in_use")]
        avail_n = A.search_count(avail_dom)
        inuse_n = A.search_count(inuse_dom)
        if not avail_n and not inuse_n:
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Tidak ada aset “<b>%s</b>” "
                            "tercatat. Coba kata yang lebih umum "
                            "(mis. <i>kabel, mouse, tinta</i>), ketik "
                            "<i>“stok menipis”</i>, atau "
                            "<i>“rekap aset”</i>.</div>" % _esc(kw)}
        if not avail_n:
            rows = A.search_read(inuse_dom, _ASSET_FIELDS, limit=10,
                                 order="id desc")
            return {"html": self._asset_table(
                "📦 Stok “<b>%s</b>” kosong — semua <b>%d</b> sedang "
                "dipakai:" % (_esc(kw), inuse_n), rows)
                + "<div class='ai-foot'>Balas tag-nya untuk cek siapa "
                  "pemakainya.</div>",
                    "tool": "check_stock"}
        rows = A.search_read(avail_dom, _ASSET_FIELDS, limit=15,
                             order="id desc")
        extra = "" if avail_n <= 15 else \
            "<div class='ai-foot'>Menampilkan 15 dari %d.</div>" % avail_n
        return {"html": self._asset_table(
            "📦 Stok “<b>%s</b>” — <b>%d</b> tersedia%s:" % (
                _esc(kw), avail_n,
                " • %d dipakai" % inuse_n if inuse_n else ""), rows) + extra,
            "tool": "check_stock",
            "action": self._list_action("Assets", "it_asset.asset",
                                       avail_dom)}

    def _stock_table(self, title, rows, footer=""):
        parts = [title, "<div class='ai-table-wrap'><table class='ai-table'>"
                        "<thead><tr><th>Barang</th><th>Sisa</th><th>Min</th>"
                        "<th>Status</th></tr></thead><tbody>"]
        for c in rows:
            qty = c["qty_available"] or 0
            minimum = c["min_quantity"] or 0
            uom = _m2o(c.get("uom_id"))
            badge = ("<span class='ai-badge bad'>Habis</span>" if qty <= 0
                     else "<span class='ai-badge warn'>Menipis</span>"
                     if qty <= minimum else
                     "<span class='ai-badge ok'>Aman</span>")
            parts.append(
                "<tr><td><b>%s</b><div class='ai-sub'>%s</div></td>"
                "<td class='ai-num'>%s %s</td><td class='ai-num'>%s</td>"
                "<td>%s</td></tr>" % (
                    _esc(c["name"]), _esc(_m2o(c.get("product_id"))),
                    qty, _esc("" if uom in ("", "-") else uom),
                    minimum, badge))
        parts.append("</tbody></table></div>")
        if footer:
            parts.append("<div class='ai-foot'>%s</div>" % footer)
        return "".join(parts)

    def _tool_asset_search(self, text):
        entities, _c = self._merged_entities(text)
        # V2: kategori fleet -> cari ke it_asset.unit (bukan category_id aset).
        cat = (entities.get("category") or "").lower()
        if cat in _FLEET_CATEGORIES:
            return self._tool_unit_search(text, entities)
        domain = []
        labels = []
        if entities["state"]:
            domain.append(("state", "=", entities["state"]))
            labels.append(_STATE_LABEL[entities["state"]])
        if entities["condition"]:
            domain.append(("condition", "=", entities["condition"]))
            labels.append("kondisi " + _COND_LABEL[entities["condition"]])
        if entities["category"]:
            domain.append(("category_id.name", "ilike", entities["category"]))
            labels.append("kategori “%s”" % entities["category"])
        at = (entities.get("asset_type") or "")
        if at in ("it", "operation"):
            domain.append(("asset_type", "=", at))
            labels.append("aset %s" % _ASSET_TYPE_LABEL[at])
        # HT vs Rig: saring nama/produk; kalau tak ada yang cocok, jatuh kembali
        # ke semua radio (jujur berlabel) daripada data_miss yang menyesatkan.
        kind = (entities.get("radio_kind") or "")
        kind_domain, kind_label, kind_note = [], "", ""
        if kind == "rig":
            kind_domain = ["|", ("name", "ilike", "rig"),
                           ("product_id.name", "ilike", "rig")]
            kind_label = "Radio Rig"
        elif kind == "ht":
            kind_domain = ["|", ("name", "ilike", "HT"),
                           ("product_id.name", "ilike", "HT")]
            kind_label = "Radio HT"
        if not domain and not kind_domain:
            return None
        A = self.env["it_asset.asset"]
        if kind_domain:
            if A.search_count(domain + kind_domain):
                domain = domain + kind_domain
                labels.append(kind_label)
            elif domain:
                kind_note = ("<div class='ai-foot'>Tidak ada yang namanya "
                             "persis “<b>%s</b>” — menampilkan semua radio "
                             "yang cocok filter.</div>" % _esc(kind_label))
            else:
                return {"miss": True,
                        "hint": "<div class='ai-foot'>Belum ada data %s. "
                                "Coba <i>“rekap aset”</i>.</div>" % _esc(kind_label)}
        total = A.search_count(domain)
        if not total:
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Coba longgarkan filter atau "
                            "ketik <i>“rekap aset”</i>.</div>"}
        rows = A.search_read(domain, _ASSET_FIELDS, limit=15, order="id desc")
        extra = ""
        if total > len(rows):
            extra = ("<div class='ai-foot'>Menampilkan %d dari %d aset. "
                     "Persempit dengan tag, mis: <i>“riwayat %s”</i></div>"
                     % (len(rows), total,
                        _esc(rows[0].get("asset_tag") or rows[0].get("name") or "")))
        return {"html": self._asset_table(
            "🔎 <b>%d</b> aset — %s:" % (total, _esc(" • ".join(labels))),
            rows) + extra + kind_note, "tool": "asset_search",
            "action": self._list_action("Assets", "it_asset.asset", domain)}

    def _asset_domain_for(self, keyword):
        """Domain OR antar-varian fuzzy (V2 goals2.md §9).

        'DT-02' juga dicari sebagai 'DT02'/'DT 02'/'DT.02'/'DT-2' agar
        'dt 02' ketemu 'DT-02' di DB. Unit ikut dicari via unit_id.name.
        """
        try:
            variants = nlu.ref_variants(keyword)
        except AttributeError:
            variants = [keyword]
        if not variants:
            variants = [keyword]
        # batasi agar domain tidak meledak (varian sudah dedup di NLU)
        variants = variants[:12]
        conds = []
        for v in variants:
            if not v or len(v) < 2:
                continue
            conds.extend([
                ("name", "ilike", v),
                ("asset_tag", "ilike", v),
                ("lot_id.name", "ilike", v),
                ("employee_id.name", "ilike", v),
                ("unit_id.name", "ilike", v),
            ])
        if not conds:
            kw = keyword or ""
            conds = [("name", "ilike", kw), ("asset_tag", "ilike", kw)]
        return _or_domain(conds)

    def _unit_domain_for(self, keyword):
        try:
            variants = nlu.ref_variants(keyword)
        except AttributeError:
            variants = [keyword]
        if not variants:
            variants = [keyword]
        variants = variants[:12]
        conds = []
        for v in variants:
            if not v or len(v) < 2:
                continue
            conds.extend([
                ("name", "ilike", v),
                ("category_id.name", "ilike", v),
            ])
        return _or_domain(conds) if conds else []

    def _tool_unit_search(self, text, entities):
        """Cari fleet/unit (V2): 'exca' -> Excavator, 'dt 02' -> DT-02."""
        U = self.env["it_asset.unit"]
        low = (text or "").lower()
        unit_state = ""
        for st in ("breakdown", "standby", "ready"):
            if st in low:
                unit_state = st
                break
        # Jika ada ref spesifik (DT-02), cari unit itu dulu.
        refs = entities.get("asset_refs") or []
        if refs:
            kw = refs[0]
            units = U.search_read(self._unit_domain_for(kw),
                                  ["name", "category_id", "state", "brand",
                                   "model"], limit=10, order="name asc")
            if units:
                return {"html": self._unit_table(
                    "🚜 Unit <b>%s</b> (%d):" % (_esc(kw), len(units)),
                    units), "tool": "asset_search",
                    "action": self._list_action(
                        "Units", "it_asset.unit",
                        self._unit_domain_for(kw))}
            # jatuh ke bawah: mungkin maksudnya aset yang terpasang di unit itu
            A = self.env["it_asset.asset"]
            rows = A.search_read(self._asset_domain_for(kw), _ASSET_FIELDS,
                                 limit=15, order="id desc")
            if rows:
                return {"html": self._asset_table(
                    "🔎 Aset terpasang pada unit mirip “<b>%s</b>”:" % _esc(kw),
                    rows), "tool": "asset_search"}
        cat = (entities.get("category") or "")
        domain = []
        if cat and cat.lower() not in ("fleet", "unit"):
            domain.append(("category_id.name", "ilike", cat))
        if unit_state:
            domain.append(("state", "=", unit_state))
        if not domain:
            return None
        total = U.search_count(domain)
        if not total:
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Belum ada unit cocok. "
                            "Coba <i>“rekap aset”</i>.</div>"}
        rows = U.search_read(domain, ["name", "category_id", "state",
                                      "brand", "model"], limit=15,
                             order="name asc")
        label = "kategori “%s”" % cat if cat else "unit"
        if unit_state:
            label += " • %s" % _UNIT_STATE_MAP.get(unit_state, unit_state)
        return {"html": self._unit_table(
            "🚜 <b>%d</b> unit operasional — %s:" % (total, _esc(label)), rows),
            "tool": "asset_search",
            "action": self._list_action("Units", "it_asset.unit", domain)}

    def _unit_table(self, title, rows):
        parts = [title, "<div class='ai-table-wrap'><table class='ai-table'>"
                        "<thead><tr><th>Unit</th><th>Kategori</th>"
                        "<th>Status</th></tr></thead><tbody>"]
        for u in rows:
            parts.append(
                "<tr><td><b>%s</b><div class='ai-sub'>%s %s</div></td>"
                "<td>%s</td><td><span class='ai-badge info'>%s</span></td></tr>"
                % (_esc(u.get("name") or "-"),
                   _esc(u.get("brand") or ""), _esc(u.get("model") or ""),
                   _esc(_m2o(u.get("category_id")) or "-"),
                   _esc(_UNIT_STATE_MAP.get(u.get("state"), u.get("state") or "-"))))
        parts.append("</tbody></table></div>")
        return "".join(parts)

    def _tool_asset_detail(self, text):
        entities, _c = self._merged_entities(text)
        kw = ""
        if entities["asset_refs"]:
            kw = entities["asset_refs"][0]
        elif entities["item"]:
            kw = entities["item"]
        elif entities["category"]:
            kw = entities["category"]
        if not kw or len(kw) < 2:
            return None
        A = self.env["it_asset.asset"]
        found = A.search_read(self._asset_domain_for(kw), _ASSET_FIELDS,
                              limit=10, order="id desc")
        if not found:
            # V2: kalau kode mirip fleet (DT-02) tapi bukan aset, coba unit.
            try:
                units = self.env["it_asset.unit"].search_read(
                    self._unit_domain_for(kw),
                    ["name", "category_id", "state", "brand", "model"],
                    limit=5, order="name asc")
            except Exception:
                units = []
            if units:
                return {"html": self._unit_table(
                    "🚜 Unit mirip “<b>%s</b>”:" % _esc(kw), units)
                    + "<div class='ai-foot'>Belum ada aset terpasang yang "
                      "cocok — ini data unitnya.</div>", "tool": "asset_detail"}
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Periksa kode tag/serialnya "
                            "(mis. <i>ITLT-002</i>, <i>DT-02</i>), "
                            "atau ketik <i>“rekap aset”</i>.</div>"}
        if len(found) > 1:
            total = A.search_count(self._asset_domain_for(kw))
            ids = ([a["id"] for a in found]
                   + A.search(self._asset_domain_for(kw), limit=_PENDING_CAP,
                              offset=len(found)).ids)[:_PENDING_CAP]
            return {"html": self._confirm_html(
                        nlu.INTENT_ASSET_DETAIL, kw, found[:8], total),
                    "tool": "asset_detail",
                    "pending_action": nlu.INTENT_ASSET_DETAIL,
                    "pending_ids": ids,
                    "pending_label": kw,
                    "suggestions": self._suggest_tags(found, ["semua"])}
        a = found[0]
        assigns = self.env["it_asset.assignment"].search_read(
            [("asset_id", "=", a["id"])], _ASSIGN_FIELDS, limit=5,
            order="assignment_date desc")
        maints = self.env["it_asset.maintenance"].search_read(
            [("asset_id", "=", a["id"])], _MAINT_FIELDS, limit=5,
            order="maintenance_date desc")
        damages = self.env["it_asset.damage_report"].search_read(
            [("asset_id", "=", a["id"])],
            ["name", "damage_type", "report_date", "state"], limit=5,
            order="report_date desc")
        handovers = self.env["it_asset.handover"].search_read(
            [("asset_id", "=", a["id"])],
            ["name", "receiver_id", "handover_date", "state"], limit=5,
            order="handover_date desc")
        if a.get("employee_id"):
            user = _m2o(a["employee_id"])
        elif a.get("unit_id"):
            user = "Unit " + _m2o(a["unit_id"])
        else:
            user = "Belum di-assign (di gudang IT)"
        parts = [
            "<div class='ai-detail-head'>",
            ("<span class='ai-tag big'>%s</span>" % _esc(a["asset_tag"])
             if a.get("asset_tag") else ""),
            "<b>%s</b></div>" % _esc(a.get("name") or "-"),
            "<div class='ai-kv'>"
            "<div><span>Pengguna</span><b>%s</b></div>"
            "<div><span>Status</span>%s</div>"
            "<div><span>Kondisi</span>%s</div>"
            "<div><span>Kategori</span><b>%s</b></div>"
            "<div><span>Tipe</span><b>%s</b></div>"
            "<div><span>Model</span><b>%s</b></div>"
            "<div><span>Serial</span><b>%s</b></div>"
            "<div><span>Produk</span><b>%s</b></div>"
            "</div>" % (_esc(user), self._state_badge(a.get("state")),
                        self._cond_badge(a.get("condition")),
                        _esc(_m2o(a.get("category_id")) or "-"),
                        _esc("%s • %s" % (
                            _ASSET_TYPE_LABEL.get(a.get("asset_type"), "-"),
                            _IT_TYPE_LABEL.get(a.get("it_type"), "-"))),
                        _esc(a.get("model") or "-"),
                        _esc(_m2o(a.get("lot_id")) or "-"),
                        _esc(_m2o(a.get("product_id")) or "-")),
            ("<div class='ai-sec'>📋 <b>Spesifikasi</b></div>"
             "<div class='ai-sub'>%s</div>" % _esc(a.get("specification") or "-")
             if a.get("specification") else ""),
            "<div class='ai-sec'>📜 <b>Riwayat pengguna (%s)</b></div>" % (
                len(assigns) or "belum ada"),
        ]
        if assigns:
            parts.append("<div class='ai-timeline'>")
            for s in assigns:
                parts.append(
                    "<div class='ai-tl-row'><i class='fa fa-user'></i><div><b>%s</b>"
                    "<div class='ai-sub'>%s → %s • %s</div></div></div>" % (
                        _esc(_m2o(s.get("employee_id")) or "-"),
                        _esc(s.get("assignment_date") or "-"),
                        _esc(s.get("return_date") or "sekarang"),
                        _esc(s.get("state") or "-")))
            parts.append("</div>")
        else:
            parts.append("<div class='ai-sub'>Belum pernah di-assign ke karyawan.</div>")
        parts.append(
            "<div class='ai-sec'>🔧 <b>Maintenance (%d)</b> • 📝 "
            "<b>Damage (%d)</b> • 🤝 <b>Handover (%d)</b></div>" % (
                len(maints), len(damages), len(handovers)))
        if maints:
            parts.append("<div class='ai-timeline'>")
            for m in maints[:3]:
                parts.append(
                    "<div class='ai-tl-row'><i class='fa fa-wrench'></i><div><b>%s — %s</b>"
                    "<div class='ai-sub'>%s%s</div></div></div>" % (
                        _esc(m.get("maintenance_date") or "-"),
                        _esc(m.get("maintenance_type") or ""),
                        _esc((m.get("description") or "")[:90]),
                        (" • Rp%s" % m["cost"]) if m.get("cost") else ""))
            parts.append("</div>")
        else:
            parts.append("<div class='ai-sub'>Belum ada riwayat maintenance. "
                         "Damage: %d, Handover: %d.</div>"
                         % (len(damages), len(handovers)))
        parts.append(
            "<div class='ai-foot'>Buka detail lengkap di menu "
            "<b>Inventory → All</b> lalu cari tag <b>%s</b>.</div>"
            % _esc(a.get("asset_tag") or a.get("name") or ""))
        return {"html": "".join(parts), "tool": "asset_detail",
                "action": self._list_action(
                    a.get("asset_tag") or a.get("name") or "Asset",
                    "it_asset.asset", [("id", "=", a["id"])])}

    @staticmethod
    def _single_user_html(a):
        user = (_m2o(a["employee_id"]) if a.get("employee_id")
                else ("Unit " + _m2o(a["unit_id"]) if a.get("unit_id")
                      else "Belum di-assign (di gudang IT)"))
        return ("👤 <b>%s — %s</b> saat ini dipegang oleh <b>%s</b>."
                "<br/><div class='ai-sub'>Status: %s • Kondisi: %s</div>" % (
                    _esc(a.get("asset_tag") or "-"),
                    _esc(a.get("name") or "-"), _esc(user),
                    _esc(_STATE_LABEL.get(a.get("state"), "-")),
                    _esc(_COND_LABEL.get(a.get("condition"), "-"))))

    def _user_domain_from_entities(self, entities):
        """Domain untuk 'siapa pakai <kategori/domain>' (tanpa ref spesifik)."""
        domain, labels = [], []
        cat = (entities.get("category") or "")
        if cat:
            domain.append(("category_id.name", "ilike", cat))
            labels.append(cat)
        at = (entities.get("asset_type") or "")
        if at in ("it", "operation"):
            domain.append(("asset_type", "=", at))
            labels.append("aset %s" % _ASSET_TYPE_LABEL[at])
        for key in ("state", "condition"):
            if entities.get(key):
                domain.append((key, "=", entities[key]))
        kind = (entities.get("radio_kind") or "")
        kind_domain = []
        if kind == "rig":
            kind_domain = ["|", ("name", "ilike", "rig"),
                           ("product_id.name", "ilike", "rig")]
        elif kind == "ht":
            kind_domain = ["|", ("name", "ilike", "HT"),
                           ("product_id.name", "ilike", "HT")]
        return domain, labels, kind_domain, kind

    def _tool_asset_user(self, text):
        entities, _c = self._merged_entities(text)
        A = self.env["it_asset.asset"]
        if entities["asset_refs"]:
            kw = entities["asset_refs"][0]
            domain = self._asset_domain_for(kw)
            total = A.search_count(domain)
            if not total:
                return {"miss": True, "hint": ""}
            if total > 1:
                found = A.search_read(domain, _ASSET_FIELDS, limit=8,
                                      order="id desc")
                ids = ([a["id"] for a in found]
                       + A.search(domain, limit=_PENDING_CAP,
                                  offset=len(found)).ids)[:_PENDING_CAP]
                return {"html": self._confirm_html(
                            nlu.INTENT_ASSET_USER, kw, found, total),
                        "tool": "asset_user",
                        "pending_action": nlu.INTENT_ASSET_USER,
                        "pending_ids": ids,
                        "pending_label": kw,
                        "suggestions": self._suggest_tags(found, ["semua"])}
            a = A.search_read(domain, _ASSET_FIELDS, limit=1)[0]
            return {"html": self._single_user_html(a),
                    "tool": "asset_user"}
        if entities["employee_name"]:
            name = entities["employee_name"]
            rows = A.search_read([("employee_id.name", "ilike", name)],
                                 _ASSET_FIELDS, limit=15)
            if rows:
                return {"html": self._asset_table(
                    "👤 Aset yang dipegang <b>%s</b> (%d):" % (
                        _esc(name), len(rows)), rows), "tool": "asset_user"}
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Cek ejaan nama karyawannya, "
                            "atau cari by tag aset (<i>“siapa pakai ITLT-…”</i>).</div>"}
        domain, labels, kind_domain, kind = \
            self._user_domain_from_entities(entities)
        if domain:
            # "radio rig siapa yang pakai" -> jawab + tawarkan spesifikasi
            total = (A.search_count(domain + kind_domain)
                     if kind_domain else A.search_count(domain))
            use_kind = bool(kind_domain and total)
            if kind_domain and not total:
                total = A.search_count(domain)
            if total == 1:
                eff = domain + kind_domain if use_kind else domain
                a = A.search_read(eff, _ASSET_FIELDS, limit=1)[0]
                return {"html": self._single_user_html(a),
                        "tool": "asset_user"}
            if total > 1:
                eff = domain + kind_domain if use_kind else domain
                rows = A.search_read(eff, _ASSET_FIELDS, limit=8,
                                     order="id desc")
                ids = ([a["id"] for a in rows]
                       + A.search(eff, limit=_PENDING_CAP,
                                  offset=len(rows)).ids)[:_PENDING_CAP]
                title = "👤 Pengguna %s (<b>%d</b>):" % (
                    _esc(" • ".join(labels)) or "aset", total)
                foot = ("<div class='ai-foot'>Mau detail salah satunya? "
                        "Balas <b>nomor aset / SN</b>-nya."
                        + (" Atau ketik <i>“semua”</i> untuk tampilkan "
                           "semuanya (%d)." % total if total > 8 else "")
                        + "</div>")
                return {"html": self._asset_table(title, rows) + foot,
                        "tool": "asset_user",
                        "pending_action": nlu.INTENT_ASSET_USER,
                        "pending_ids": ids,
                        "pending_label": " • ".join(labels),
                        "suggestions": self._suggest_tags(
                            rows, ["semua"] if total > 8 else [])}
            # total == 0 -> jatuh ke item fallback di bawah
        if entities["item"] and len(entities["item"]) >= 3:
            rows = A.search_read(self._asset_domain_for(entities["item"]),
                                 _ASSET_FIELDS, limit=8, order="id desc")
            if rows:
                return {"html": self._asset_table(
                    "Kandidat aset untuk “<b>%s</b>” — pengguna tertera di "
                    "kolom Pengguna:" % _esc(entities["item"]), rows),
                    "tool": "asset_user"}
        return None

    def _tool_asset_history(self, text):
        entities, _c = self._merged_entities(text)
        Assign = self.env["it_asset.assignment"]
        if entities["asset_refs"]:
            kw = entities["asset_refs"][0]
            domain = self._asset_domain_for(kw)
            total = self.env["it_asset.asset"].search_count(domain)
            if not total:
                return {"miss": True, "hint": ""}
            if total > 1:
                found = self.env["it_asset.asset"].search_read(
                    domain, ["id", "name", "asset_tag"], limit=8,
                    order="id desc")
                ids = ([a["id"] for a in found]
                       + self.env["it_asset.asset"].search(
                           domain, limit=_PENDING_CAP,
                           offset=len(found)).ids)[:_PENDING_CAP]
                return {"html": self._confirm_html(
                            nlu.INTENT_ASSET_HISTORY, kw, found, total),
                        "tool": "asset_history",
                        "pending_action": nlu.INTENT_ASSET_HISTORY,
                        "pending_ids": ids,
                        "pending_label": kw,
                        "suggestions": self._suggest_tags(found, ["semua"])}
            a = self.env["it_asset.asset"].search_read(
                domain, ["id", "name", "asset_tag"], limit=1)[0]
            rows = Assign.search_read(
                [("asset_id", "=", a["id"])], _ASSIGN_FIELDS, limit=10,
                order="assignment_date desc")
            if not rows:
                return {"html":
                        "📜 <b>%s — %s</b> belum punya riwayat assignment." % (
                            _esc(a.get("asset_tag") or "-"),
                            _esc(a.get("name") or "-")),
                        "tool": "asset_history"}
            parts = ["📜 <b>Riwayat %s — %s</b> (%d):"
                     "<div class='ai-timeline'>" % (
                         _esc(a.get("asset_tag") or "-"),
                         _esc(a.get("name") or "-"), len(rows))]
            for s in rows:
                parts.append(
                    "<div class='ai-tl-row'><i class='fa fa-user'></i><div><b>%s</b>"
                    "<div class='ai-sub'>%s → %s • %s</div></div></div>" % (
                        _esc(_m2o(s.get("employee_id")) or "-"),
                        _esc(s.get("assignment_date") or "-"),
                        _esc(s.get("return_date") or "sekarang"),
                        _esc(s.get("state") or "-")))
            parts.append("</div>")
            return {"html": "".join(parts), "tool": "asset_history"}
        rows = Assign.search_read([], _ASSIGN_FIELDS, limit=10,
                                  order="assignment_date desc")
        if not rows:
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Belum ada assignment tercatat.</div>"}
        parts = ["📜 <b>Mutasi aset terakhir:</b>"
                 "<div class='ai-table-wrap'><table class='ai-table'>"
                 "<thead><tr><th>Aset</th><th>Karyawan</th><th>Periode</th></tr></thead><tbody>"]
        for s in rows:
            parts.append("<tr><td><b>%s</b></td><td>%s</td><td>%s → %s</td></tr>" % (
                _esc(_m2o(s.get("asset_id")) or "-"),
                _esc(_m2o(s.get("employee_id")) or "-"),
                _esc(s.get("assignment_date") or "-"),
                _esc(s.get("return_date") or "sekarang")))
        parts.append("</tbody></table></div>"
                     "<div class='ai-foot'>Ketik <i>“riwayat [tag aset]”</i> "
                     "untuk riwayat per aset.</div>")
        return {"html": "".join(parts), "tool": "asset_history"}

    @staticmethod
    def _age_label(create_date):
        try:
            day = str(create_date or "")[:10]
            y, m, d = int(day[0:4]), int(day[5:7]), int(day[8:10])
            delta = (_datetime.date.today() - _datetime.date(y, m, d)).days
        except (TypeError, ValueError):
            return "-"
        if delta >= 730:
            return "%d thn" % (delta // 365)
        if delta >= 60:
            return "%d bln" % (delta // 30)
        return "%d hari" % max(delta, 0)

    def _top_base_domain(self, entities):
        """Filter kategori/domain untuk ranking (ikut topik sesi bila ada)."""
        domain = []
        cat = (entities.get("category") or "")
        if cat:
            domain.append(("category_id.name", "ilike", cat))
        at = (entities.get("asset_type") or "")
        if at in ("it", "operation"):
            domain.append(("asset_type", "=", at))
        kind = (entities.get("radio_kind") or "")
        if kind == "rig":
            domain.extend(["|", ("name", "ilike", "rig"),
                           ("product_id.name", "ilike", "rig")])
        elif kind == "ht":
            domain.extend(["|", ("name", "ilike", "HT"),
                           ("product_id.name", "ilike", "HT")])
        return domain

    def _tool_asset_top(self, text):
        """Ranking: paling tua/baru (create_date), tersering pindah (assignment),
        tersering rusak (damage). Hormati filter kategori/domain sesi."""
        entities, _c = self._merged_entities(text)
        kind = entities.get("top_kind") or ""
        if kind not in ("oldest", "newest", "moved", "damaged"):
            return None
        A = self.env["it_asset.asset"]
        base = self._top_base_domain(entities)
        if kind in ("oldest", "newest"):
            order = "create_date asc" if kind == "oldest" else "create_date desc"
            total = A.search_count(base)
            if not total:
                return {"miss": True,
                        "hint": "<div class='ai-foot'>Belum ada aset cocok "
                                "filter.</div>"}
            rows = A.search_read(base, _ASSET_FIELDS + ["create_date"],
                                 limit=10, order=order)
            title = ("⏳ Aset paling tua" if kind == "oldest"
                     else "✨ Aset paling baru")
            foot = "<div class='ai-foot'>" + "<br/>".join(
                "%s — %s (%s)" % (
                    _esc(a.get("asset_tag") or "-"),
                    _esc(str(a.get("create_date") or "-")[:10]),
                    self._age_label(a.get("create_date")))
                for a in rows[:5]) + "</div>"
            return {"html": self._asset_table(
                        "%s (<b>%d</b>):" % (title, total), rows) + foot,
                    "tool": "asset_top"}
        # moved / damaged via read_group hitung per aset
        model = ("it_asset.assignment" if kind == "moved"
                 else "it_asset.damage_report")
        aids = A.search(base, limit=2000).ids if base else []
        gdom = [("asset_id", "in", aids)] if base else []
        if base and not aids:
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Belum ada aset cocok "
                            "filter.</div>"}
        try:
            groups = self.env[model].read_group(
                gdom, ["asset_id"], ["asset_id"],
                orderby="asset_id_count desc", limit=10)
        except Exception as exc:
            _logger.warning("Ask AI top-group gagal: %s", exc)
            return None
        groups = [g for g in groups if g.get("asset_id")]
        if not groups:
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Belum ada data %s tercatat.</div>"
                            % ("perpindahan" if kind == "moved"
                               else "kerusakan")}
        ids = [g["asset_id"][0] for g in groups]
        counts = {g["asset_id"][0]: g.get("asset_id_count", 0) for g in groups}
        rows = A.search_read([("id", "in", ids)], _ASSET_FIELDS)
        by_id = {a["id"]: a for a in rows}
        ordered = [by_id[i] for i in ids if i in by_id]
        unit = "x pindah" if kind == "moved" else "x rusak"
        title = ("🔁 Paling sering pindah" if kind == "moved"
                 else "🔥 Paling sering rusak")
        foot = "<div class='ai-foot'>" + "<br/>".join(
            "%s — %d %s" % (
                _esc((by_id[i].get("asset_tag")) or "-"),
                counts.get(i, 0), unit)
            for i in ids if i in by_id) + "</div>"
        return {"html": self._asset_table(
                    "%s:" % title, ordered) + foot,
                "tool": "asset_top"}

    def _tool_maintenance_list(self, _text):
        rows = self.env["it_asset.maintenance"].search_read(
            [], _MAINT_FIELDS + ["create_date"], limit=10,
            order="maintenance_date desc")
        if not rows:
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Riwayat servis akan muncul di "
                            "sini setelah teknisi mencatatnya.</div>"}
        parts = ["🔧 <b>Maintenance terakhir (%d):</b>"
                 "<div class='ai-table-wrap'><table class='ai-table'>"
                 "<thead><tr><th>Tanggal</th><th>Aset</th><th>Deskripsi</th></tr>"
                 "</thead><tbody>" % len(rows)]
        for m in rows:
            parts.append("<tr><td>%s</td><td><b>%s</b><div class='ai-sub'>%s%s</div></td><td>%s</td></tr>" % (
                _esc(m.get("maintenance_date") or "-"),
                _esc(_m2o(m.get("asset_id")) or "-"),
                _esc(m.get("maintenance_type") or ""),
                (" • Rp%s" % m["cost"]) if m.get("cost") else "",
                _esc((m.get("description") or "-")[:80])))
        parts.append("</tbody></table></div><div class='ai-foot'>Ketik "
                     "<i>“riwayat [nama aset]”</i> untuk maintenance per aset.</div>")
        return {"html": "".join(parts), "tool": "maintenance_list",
                "action": self._list_action(
                    "Maintenance", "it_asset.maintenance", [])}

    # ------------------------------------------------------------------
    # form: periode + status (goals2.md §13)
    # ------------------------------------------------------------------
    def _period_range(self, period):
        """(date_from, date_to) untuk filter tanggal, atau (None, None)."""
        if not period:
            return None, None
        today = _datetime.date.today()
        if period == "today":
            return today, today
        if period == "yesterday":
            y = today - _datetime.timedelta(days=1)
            return y, y
        if period == "this_week":
            return today - _datetime.timedelta(days=today.weekday()), today
        if period == "this_month":
            return today.replace(day=1), today
        if period == "last_month":
            first_this = today.replace(day=1)
            last_prev = first_this - _datetime.timedelta(days=1)
            return last_prev.replace(day=1), last_prev
        return None, None

    @staticmethod
    def _request_states(fs):
        """Status teknis form request (material/aset/akun)."""
        if fs == "done":
            return ["fulfilled"]
        if fs == "open":
            return ["draft", "submitted", "approved", "partially_fulfilled"]
        if fs in ("draft", "submitted", "approved", "partially_fulfilled",
                  "fulfilled", "rejected"):
            return [fs]
        return []

    def _form_filter_label(self, entities):
        bits = []
        fs = entities.get("form_status") or ""
        if fs:
            bits.append(_FORM_STATE_LABEL.get(fs, fs))
        period = entities.get("period") or ""
        if period:
            bits.append(_PERIOD_LABEL.get(period, period))
        return " • ".join(bits)

    @staticmethod
    def _state_badge_for(state):
        cls = {"fulfilled": "ok", "signed": "ok", "resolved": "ok",
               "approved": "info", "confirmed": "info",
               "submitted": "warn", "partially_fulfilled": "warn",
               "draft": "muted", "rejected": "bad"}.get(state, "muted")
        return "<span class='ai-badge %s'>%s</span>" % (cls, _esc(state or "-"))

    def _tool_handover_list(self, text):
        entities, _c = self._merged_entities(text)
        fs = entities.get("form_status") or ""
        dfrom, dto = self._period_range(entities.get("period") or "")
        hstates = {"signed": ["signed"], "draft": ["draft"],
                   "done": ["signed"], "open": ["draft"]}.get(fs, [])
        asset_ids = []
        refs = entities.get("asset_refs") or []
        if refs:
            asset_ids = self.env["it_asset.asset"].search(
                self._asset_domain_for(refs[0]), limit=10).ids
        hdom = []
        if hstates:
            hdom.append(("state", "in", hstates))
        if dfrom:
            hdom.append(("handover_date", ">=", dfrom))
        if dto:
            hdom.append(("handover_date", "<=", dto))
        if asset_ids:
            hdom.append(("asset_id", "in", asset_ids))
        hrows = self.env["it_asset.handover"].search_read(
            hdom, ["name", "asset_id", "receiver_id", "handover_date",
                   "state"], limit=10, order="handover_date desc")
        idom = []
        if hstates:
            idom.append(("state", "in", hstates))
        if dfrom:
            idom.append(("handover_date", ">=", dfrom))
        if dto:
            idom.append(("handover_date", "<=", dto))
        irows = self.env["it_asset.item.handover"].search_read(
            idom, ["name", "receiver_id", "handover_date", "state",
                   "total_items"], limit=10, order="handover_date desc")
        if not hrows and not irows:
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Belum ada serah terima "
                            "cocok filter. Coba longgarkan periode/status.</div>"}
        flabel = self._form_filter_label(entities)
        title = "🤝 <b>Serah terima%s:</b>" % (
            " (%s)" % _esc(flabel) if flabel else "")
        parts = [title]
        if hrows:
            parts.append("<div class='ai-sec'>Handover aset (BAST)</div>"
                         "<div class='ai-table-wrap'><table class='ai-table'>"
                         "<thead><tr><th>Ref</th><th>Aset → Penerima</th>"
                         "<th>Tgl</th><th>Status</th></tr></thead><tbody>")
            for h in hrows:
                parts.append("<tr><td>%s</td><td><b>%s</b><div class='ai-sub'>→ %s</div></td>"
                             "<td>%s</td><td>%s</td></tr>" % (
                                 _esc(h.get("name") or "-"),
                                 _esc(_m2o(h.get("asset_id")) or "-"),
                                 _esc(_m2o(h.get("receiver_id")) or "-"),
                                 _esc(h.get("handover_date") or "-"),
                                 self._state_badge_for(h.get("state"))))
            parts.append("</tbody></table></div>")
        if irows:
            parts.append("<div class='ai-sec'>Item handover (multi-item)</div>"
                         "<div class='ai-table-wrap'><table class='ai-table'>"
                         "<thead><tr><th>Ref</th><th>Penerima</th>"
                         "<th>Tgl</th><th>Status</th></tr></thead><tbody>")
            for h in irows:
                parts.append("<tr><td>%s<div class='ai-sub'>%s item</div></td>"
                             "<td><b>%s</b></td><td>%s</td><td>%s</td></tr>" % (
                                 _esc(h.get("name") or "-"),
                                 h.get("total_items") or 0,
                                 _esc(_m2o(h.get("receiver_id")) or "-"),
                                 _esc(h.get("handover_date") or "-"),
                                 self._state_badge_for(h.get("state"))))
            parts.append("</tbody></table></div>")
        return {"html": "".join(parts), "tool": "handover_list",
                "action": self._list_action(
                    "Handovers", "it_asset.handover", hdom)}

    def _request_section(self, model, title, states, dfrom, dto):
        M = self.env[model]
        base = []
        if states:
            base.append(("state", "in", states))
        if dfrom:
            base.append(("request_date", ">=", dfrom))
        if dto:
            base.append(("request_date", "<=", dto))
        rows = M.search_read(
            base, ["name", "employee_id", "request_date", "state"], limit=10,
            order="request_date desc")
        done_n = M.search_count(base + [("state", "=", "fulfilled")]) \
            if model != "it_asset.handover" else 0
        open_n = M.search_count(
            base + [("state", "in", ["draft", "submitted", "approved",
                                    "partially_fulfilled"])])
        if not rows:
            return "", False
        parts = ["<div class='ai-sec'>%s (%d fulfilled • %d belum)</div>"
                 "<div class='ai-table-wrap'><table class='ai-table'><tbody>"
                 % (title, done_n, open_n)]
        for r in rows:
            parts.append("<tr><td><b>%s</b><div class='ai-sub'>%s • %s</div></td>"
                         "<td>%s</td></tr>" % (
                             _esc(r.get("name") or "-"),
                             _esc(_m2o(r.get("employee_id")) or "-"),
                             _esc(r.get("request_date") or "-"),
                             self._state_badge_for(r.get("state"))))
        parts.append("</tbody></table></div>")
        return "".join(parts), True

    def _tool_request_status(self, text):
        entities, _c = self._merged_entities(text)
        fk = entities.get("form_kind") or ""
        states = self._request_states(entities.get("form_status") or "")
        dfrom, dto = self._period_range(entities.get("period") or "")
        want = [fk] if fk in ("material", "asset_request", "account") else \
            ["material", "asset_request", "account"]
        specs = {"material": ("it_asset.material_request", "Material Request"),
                 "asset_request": ("it_asset.request", "Asset Request"),
                 "account": ("it_asset.account_request", "Account Request")}
        flabel = self._form_filter_label(entities)
        if fk:
            flabel = (specs[fk][1] + (" • " + flabel if flabel else "")).strip()
        parts = ["📥 <b>Status pengajuan%s:</b>" % (
            " (%s)" % _esc(flabel) if flabel else "")]
        any_rows = False
        for key in want:
            model, title = specs[key]
            html, ok = self._request_section(model, title, states, dfrom, dto)
            if ok:
                parts.append(html)
                any_rows = True
            elif fk:
                parts.append("<div class='ai-sub'>%s: belum ada data cocok "
                             "filter.</div>" % title)
        if not any_rows:
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Belum ada pengajuan cocok "
                            "filter. Coba longgarkan status/periode.</div>"}
        parts.append("<div class='ai-foot'>Sudah fulfilled vs belum dihitung "
                     "per jenis di atas.</div>")
        return {"html": "".join(parts), "tool": "request_status"}

    def _tool_damage_list(self, text):
        entities, _c = self._merged_entities(text)
        fs = entities.get("form_status") or ""
        if fs == "done":
            states = ["resolved"]
        elif fs == "open":
            states = ["draft", "confirmed"]
        elif fs in ("draft", "confirmed", "resolved"):
            states = [fs]
        else:
            states = []
        low = (text or "").lower()
        dtype = ""
        for key, words in _DAMAGE_TYPE_WORDS.items():
            if any(w in low for w in words):
                dtype = key
                break
        dfrom, dto = self._period_range(entities.get("period") or "")
        domain = []
        if states:
            domain.append(("state", "in", states))
        if dtype:
            domain.append(("damage_type", "=", dtype))
        if dfrom:
            domain.append(("report_date", ">=", dfrom))
        if dto:
            domain.append(("report_date", "<=", dto))
        refs = entities.get("asset_refs") or []
        if refs:
            aids = self.env["it_asset.asset"].search(
                self._asset_domain_for(refs[0]), limit=10).ids
            if aids:
                domain.append(("asset_id", "in", aids))
        D = self.env["it_asset.damage_report"]
        rows = D.search_read(
            domain, ["name", "asset_id", "damage_type", "report_date",
                     "state"], limit=15, order="report_date desc")
        if not rows:
            if not domain:
                return {"html": "Belum ada <b>damage report</b>. Kabar baik — "
                                "tidak ada laporan kerusakan tercatat 🎉",
                        "tool": "damage_list"}
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Tidak ada damage cocok "
                            "filter. Coba longgarkan status/periode.</div>"}
        flabel = self._form_filter_label(entities)
        if dtype:
            flabel = ((_DAMAGE_TYPE_LABEL.get(dtype, dtype) + " • " + flabel)
                      if flabel else _DAMAGE_TYPE_LABEL.get(dtype, dtype))
        parts = ["📝 <b>Damage report%s (%d):</b>"
                 "<div class='ai-table-wrap'><table class='ai-table'>"
                 "<thead><tr><th>Ref</th><th>Aset</th><th>Status</th></tr></thead><tbody>"
                 % ((" (%s)" % _esc(flabel)) if flabel else "", len(rows))]
        for d in rows:
            parts.append("<tr><td>%s<div class='ai-sub'>%s • %s</div></td><td><b>%s</b></td>"
                         "<td>%s</td></tr>" % (
                             _esc(d.get("name") or "-"),
                             _esc(_DAMAGE_TYPE_LABEL.get(d.get("damage_type"),
                                                         d.get("damage_type") or "-")),
                             _esc(d.get("report_date") or "-"),
                             _esc(_m2o(d.get("asset_id")) or "-"),
                             self._state_badge_for(d.get("state"))))
        parts.append("</tbody></table></div>")
        return {"html": "".join(parts), "tool": "damage_list"}

    def _tool_human_agent(self, text):
        return {"html": nlu.handover_reply(text)
                + "<div class='ai-foot'>Atau buat tiket via form IT yang sesuai "
                  "(Damage Report / Material Request).</div>",
                "tool": "handover_to_staff"}

    def _tool_identity(self, _text):
        """Jaring pengaman: identitas via jalur NLU/LLM (bukan fast-path rule)."""
        try:
            pkey, pname = self._persona_ctx()
        except Exception:
            pkey, pname = "alya", "Alya"
        lang = persona.detect_language(_text)
        return {"html": self._social_reply(nlu.INTENT_IDENTITY, _text, lang,
                                           pkey, pname),
                "tool": "identity"}

    def _tool_creator(self, _text):
        """Jaring pengaman: pembuat via jalur NLU/LLM (bukan fast-path rule)."""
        try:
            pkey, pname = self._persona_ctx()
        except Exception:
            pkey, pname = "alya", "Alya"
        lang = persona.detect_language(_text)
        if lang == "en":
            return {"html": persona.SOCIAL_EN["creator"], "tool": "creator"}
        return {"html": _canned_social(nlu.INTENT_CREATOR, _text),
                "tool": "creator"}

    # Peta intent → tool (cermin routeDecision/defaultToolForIntent WACS).
    _TOOLS = {
        nlu.INTENT_RECAP: _tool_recap,
        nlu.INTENT_CHECK_STOCK: _tool_check_stock,
        nlu.INTENT_UNIT_DETAIL: _tool_unit_detail,
        nlu.INTENT_IDENTITY: _tool_identity,
        nlu.INTENT_CREATOR: _tool_creator,
        nlu.INTENT_ASSET_SEARCH: _tool_asset_search,
        nlu.INTENT_ASSET_DETAIL: _tool_asset_detail,
        nlu.INTENT_ASSET_USER: _tool_asset_user,
        nlu.INTENT_ASSET_HISTORY: _tool_asset_history,
        nlu.INTENT_MAINTENANCE_LIST: _tool_maintenance_list,
        nlu.INTENT_ASSET_TOP: _tool_asset_top,
        nlu.INTENT_HANDOVER_LIST: _tool_handover_list,
        nlu.INTENT_DAMAGE_LIST: _tool_damage_list,
        nlu.INTENT_REQUEST_STATUS: _tool_request_status,
        nlu.INTENT_HUMAN_AGENT: _tool_human_agent,
    }


class ITAskAIFeedback(models.Model):
    """Antrean kurasi pesan tak-yakin (cermin tabel intent_feedback WACS §17).

    Aturan tetap: baris terkurasi tak kembali ke antrean; skrip/analisis
    membaca ``status = new`` dari yang paling sering muncul.
    """
    _name = "it_asset.ask_ai.feedback"
    _description = "Ask AI Feedback Queue"
    _order = "occurrences desc, last_seen desc"

    question = fields.Text(string="Pertanyaan", readonly=True)
    question_hash = fields.Char(string="Hash", index=True, readonly=True)
    predicted_intent = fields.Char(string="Intent", readonly=True)
    confidence = fields.Float(string="Confidence", readonly=True, digits=(3, 3))
    method = fields.Char(string="Metode", readonly=True)
    tool_executed = fields.Char(string="Tool/Aksi", readonly=True)
    capture_reason = fields.Char(string="Alasan Rekam", readonly=True)
    ood_reason = fields.Char(string="Alasan OOD", readonly=True)
    occurrences = fields.Integer(string="Kemunculan", default=1, readonly=True)
    last_seen = fields.Datetime(string="Terakhir", readonly=True)
    status = fields.Selection([("new", "Baru"), ("curated", "Terkurasi")],
                              default="new")
    curated_intent = fields.Char(string="Intent Hasil Kurasi")
    curated_not_intent = fields.Char(string="Bukan Intent")
