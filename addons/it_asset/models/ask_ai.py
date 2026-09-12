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
import time as _time
import urllib.request as _urlrequest

from odoo import api, fields, models

from . import ask_ai_nlu as nlu

_logger = logging.getLogger(__name__)

# Cooldown ala WACS (agent.unavailableCooldown): satu request gagal/timeout
# tidak boleh membuat tiap pesan berikutnya menunggu timeout penuh.
_LLM_COOLDOWN_UNTIL = 0.0
_LLM_COOLDOWN_SEC = 60.0

# Default parameter — cermin variabel WACS (WACS_QWEN_URL/MODEL/TIMEOUT_SEC).
_LLM_DEFAULTS = {
    "it_asset.ask_ai.llm_enabled": "False",
    "it_asset.ask_ai.llm_url": "http://127.0.0.1:8081",
    "it_asset.ask_ai.llm_model": nlu.LLM_MODEL_DEFAULT,
    "it_asset.ask_ai.llm_timeout": "10",
    "it_asset.ask_ai.llm_json_mode": "True",
}

_ASSET_FIELDS = [
    "name", "asset_tag", "asset_type", "it_type", "category_id",
    "product_id", "lot_id", "employee_id", "unit_id", "state",
    "condition", "usage_type",
]
_CONSUMABLE_FIELDS = ["name", "product_id", "qty_available", "min_quantity", "uom_id"]
_ASSIGN_FIELDS = ["asset_id", "employee_id", "assignment_date", "return_date", "state"]
_MAINT_FIELDS = ["asset_id", "maintenance_date", "maintenance_type",
                 "description", "cost", "technician"]

_STATE_LABEL = {"available": "Tersedia", "in_use": "Dipakai",
                "maintenance": "Out of Service", "retired": "Retired"}
_COND_LABEL = {"good": "Good", "degraded": "Degraded", "broken": "Broken"}

_SOCIAL_INTENTS = (nlu.INTENT_GREETING, nlu.INTENT_THANKS,
                   nlu.INTENT_GOODBYE, nlu.INTENT_HELP)

_CANNED_SOCIAL = {
    nlu.INTENT_GREETING: (
        "Halo! 👋 Saya membaca <b>data live modul IT</b> — stok produk, "
        "jumlah aset, pengguna, kondisi, dan riwayat.<br/>Coba: "
        "<i>“stok toner?”</i> • <i>“rekap aset”</i> • "
        "<i>“siapa yang pakai LT-012?”</i>"),
    nlu.INTENT_THANKS: "Sama-sama! 🙏 Ada lagi yang mau dicek dari data IT?",
    nlu.INTENT_GOODBYE: "Siap, sampai jumpa! 👋 Saya di menu <b>IT → Ask AI</b> kalau dibutuhkan lagi.",
    nlu.INTENT_HELP: (
        "Yang bisa saya jawab dari <b>data live</b>:<br/>"
        "<div class='ai-help'>"
        "<div>📦 <b>Stok</b> — <i>“stok toner?”</i>, <i>“stok menipis”</i></div>"
        "<div>💻 <b>Jumlah</b> — <i>“rekap aset”</i>, <i>“laptop tersedia”</i></div>"
        "<div>👤 <b>Pengguna</b> — <i>“siapa pakai LT-012?”</i></div>"
        "<div>📜 <b>Riwayat</b> — <i>“riwayat PRN-01”</i></div>"
        "</div>"),
}


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
    def answer(self, question):
        """Jawab satu pertanyaan. Selalu kembalikan dict siap-render.

        Bentuk: ``{html, intent, confidence, method, action?}`` dengan
        ``action = {res_model, domain, name}`` opsional untuk tombol
        "Lihat di modul" di frontend.
        """
        text = (question or "").strip()
        if not text:
            return self._out(text, nlu.INTENT_UNKNOWN, 0.0, "empty",
                             "Tulis dulu pertanyaannya 🙂", "clarification")

        # Klasifikasi deterministik dulu: fast-path sosial/OOD dan L2
        # override TIDAK PERNAH memanggil Qwen (cermin WACS — tiap panggilan
        # Qwen adalah round-trip CPU mahal di runtime single-slot).
        classification = nlu.classify(text)
        method = classification["method"]

        if method not in ("rule", "ood_rule", "override"):
            llm_classification = self._try_llm_decide(text)
            if llm_classification:
                classification = llm_classification
                method = "llm"

        intent = classification["intent"]
        conf = classification["confidence"]
        method = classification["method"]

        # Fast-path sosial: jawaban canned, tanpa tool (cermin WACS).
        if method == "rule" and intent in _SOCIAL_INTENTS:
            return self._out(text, intent, conf, "deterministic_answer",
                             _CANNED_SOCIAL[intent], "deterministic_answer")

        # Fast-path OOD: tolak bervariasi, tanpa tool (cermin WACS).
        if method == "ood_rule":
            reason = classification.get("ood_reason", "")
            out = self._out(text, nlu.INTENT_UNKNOWN, conf, "ood_scope_filter",
                            nlu.scope_reply(text, reason), "ood_scope_filter")
            out["ood_reason"] = reason
            self._record_feedback(text, out)
            return out

        # Entitas LLM (bila ada) hanya dipakai bila grounded — substring
        # dari teks user (cermin WACS: extractor pemilik kanal entitas,
        # model tak boleh mengarang nilai).
        llm_ctx = None
        if method == "llm":
            llm_ctx = {
                "entities": classification.get("llm_entities") or {},
                "constraints": classification.get("llm_constraints") or {},
            }
        runner = self.with_context(ask_ai_llm=llm_ctx) if llm_ctx else self

        route = nlu.route(classification)
        if route == nlu.ROUTE_HANDOVER:
            out = self._out(text, intent, conf, method,
                            nlu.handover_reply(text), "handover_to_staff",
                            handoff=True)
            self._record_feedback(text, out)
            return out
        if route == nlu.ROUTE_CLARIFY:
            entities, _constraints = self._merged_entities(text)
            out = self._out(text, intent, conf, method,
                            nlu.clarification_reply(intent, entities),
                            "clarification")
            self._record_feedback(text, out)
            return out

        # Eksekusi tool sesuai intent (cermin routeDecision WACS).
        handler = self._TOOLS.get(intent)
        if not handler:
            out = self._out(text, nlu.INTENT_UNKNOWN, 0.0, method,
                            nlu.scope_reply(text, ""), "unknown_fallback")
            self._record_feedback(text, out)
            return out
        try:
            result = handler(runner, text)
        except Exception as exc:  # tool gagal → fallback aman, bukan karangan
            _logger.warning("Ask AI tool %s gagal: %s", intent, exc)
            out = self._out(text, intent, conf, method,
                            "Maaf, saya gagal membaca data untuk itu. "
                            "Coba lagi atau persempit kata kuncinya.",
                            "tool_error")
            self._record_feedback(text, out)
            return out

        if result is None:  # tool butuh klarifikasi (entitas kurang)
            entities, _constraints = self._merged_entities(text)
            out = self._out(text, intent, conf, method,
                            nlu.clarification_reply(intent, entities),
                            "clarification")
            self._record_feedback(text, out)
            return out
        if result.get("miss"):  # data tidak ditemukan (cermin storeMiss WACS)
            out = self._out(text, intent, conf, method,
                            nlu.data_miss_reply(text)
                            + (result.get("hint") or ""), "data_miss")
            self._record_feedback(text, out)
            return out

        out = self._out(text, intent, conf, method, result["html"],
                        result.get("tool", intent),
                        action=result.get("action"))
        self._record_feedback(text, out)  # no-op bila terjawab yakin
        return out

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
        return {"intent": decision["intent"],
                "confidence": decision["confidence"],
                "category": nlu.get_intent_category(decision["intent"]),
                "method": "llm", "ood_reason": "",
                "llm_entities": decision["entities"],
                "llm_constraints": decision["constraints"]}

    # ------------------------------------------------------------------
    # perakitan output + feedback (cermin CaptureReasonFor WACS)
    # ------------------------------------------------------------------
    def _merged_entities(self, text):
        """Extractor lokal + overlay LLM yang grounded (cermin WACS:
        ``entity.Extract`` pemilik kanal, ``Decision`` hanya constraints
        tertutup; nilai model diadopsi hanya bila         tertulis di pertanyaan)."""
        entities, constraints = nlu.extract_entities(text)
        llm = self.env.context.get("ask_ai_llm") or {}
        l_ent = llm.get("entities") or {}
        if not entities["asset_refs"] and (l_ent.get("asset_ref") or "").strip():
            v = l_ent["asset_ref"].strip().upper()
            if v in (text or "").upper() and any(ch.isdigit() for ch in v):
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

    def _out(self, text, intent, conf, method, html, tool,
             action=None, handoff=False):
        out = {"html": html, "intent": intent,
               "confidence": round(float(conf), 3), "method": method,
               "tool_executed": tool, "handoff": handoff}
        if action:
            out["action"] = action
        return out

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
            tag = ("<span class='ai-tag'>%s</span> " % _esc(a["asset_tag"])
                   if a.get("asset_tag") else "")
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

    def _tool_check_stock(self, text):
        entities, constraints = self._merged_entities(text)
        kw = entities["item"]
        C = self.env["it_asset.consumable"]
        if kw:
            rows = C.search_read(
                ["|", ("name", "ilike", kw), ("product_id.name", "ilike", kw)],
                _CONSUMABLE_FIELDS, limit=20, order="name asc")
            if not rows:
                assets = self.env["it_asset.asset"].search_read(
                    ["|", ("name", "ilike", kw), ("asset_tag", "ilike", kw)],
                    _ASSET_FIELDS, limit=5)
                if assets:
                    return {"html": self._asset_table(
                        "Hasil pencarian “<b>%s</b>” (tercatat sebagai aset, "
                        "bukan consumable):" % _esc(kw), assets),
                        "tool": "check_stock"}
                return {"miss": True,
                        "hint": "<div class='ai-foot'>Coba kata yang lebih umum "
                                "(mis: <i>toner, kabel, mouse, tinta</i>) atau "
                                "ketik <i>“stok menipis”</i>.</div>"}
            return {"html": self._stock_table(
                "Stok untuk “<b>%s</b>” (%d item):" % (_esc(kw), len(rows)),
                rows), "tool": "check_stock",
                "action": self._list_action(
                    "Consumables", "it_asset.consumable",
                    ["|", ("name", "ilike", kw),
                     ("product_id.name", "ilike", kw)])}
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
        if not domain:
            return None
        A = self.env["it_asset.asset"]
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
            rows) + extra, "tool": "asset_search",
            "action": self._list_action("Assets", "it_asset.asset", domain)}

    def _asset_domain_for(self, keyword):
        return ["|", "|", "|", ("name", "ilike", keyword),
                ("asset_tag", "ilike", keyword),
                ("lot_id.name", "ilike", keyword),
                ("employee_id.name", "ilike", keyword)]

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
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Periksa kode tag/serialnya, "
                            "atau ketik <i>“rekap aset”</i>.</div>"}
        if len(found) > 1:
            total = A.search_count(self._asset_domain_for(kw))
            first = found[0].get("asset_tag") or found[0].get("name") or ""
            return {"html": self._asset_table(
                "Ditemukan <b>%d</b> aset mirip “<b>%s</b>”. Spesifikkan "
                "tag-nya, mis <i>“riwayat %s”</i>:" % (
                    total, _esc(kw), _esc(first)), found[:8]),
                    "tool": "asset_detail"}
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
            "<div><span>Serial</span><b>%s</b></div>"
            "<div><span>Produk</span><b>%s</b></div>"
            "</div>" % (_esc(user), self._state_badge(a.get("state")),
                        self._cond_badge(a.get("condition")),
                        _esc(_m2o(a.get("category_id")) or "-"),
                        _esc(_m2o(a.get("lot_id")) or "-"),
                        _esc(_m2o(a.get("product_id")) or "-")),
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

    def _tool_asset_user(self, text):
        entities, _c = self._merged_entities(text)
        A = self.env["it_asset.asset"]
        if entities["asset_refs"]:
            found = A.search_read(
                self._asset_domain_for(entities["asset_refs"][0]),
                _ASSET_FIELDS, limit=5, order="id desc")
            if not found:
                return {"miss": True, "hint": ""}
            if len(found) > 1:
                return {"html": self._asset_table(
                    "Ditemukan beberapa aset mirip — tentukan tag-nya:",
                    found[:8]), "tool": "asset_user"}
            a = found[0]
            user = (_m2o(a["employee_id"]) if a.get("employee_id")
                    else ("Unit " + _m2o(a["unit_id"]) if a.get("unit_id")
                          else "Belum di-assign (di gudang IT)"))
            return {"html":
                    "👤 <b>%s — %s</b> saat ini dipegang oleh <b>%s</b>."
                    "<br/><div class='ai-sub'>Status: %s • Kondisi: %s</div>" % (
                        _esc(a.get("asset_tag") or "-"),
                        _esc(a.get("name") or "-"), _esc(user),
                        _esc(_STATE_LABEL.get(a.get("state"), "-")),
                        _esc(_COND_LABEL.get(a.get("condition"), "-"))),
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
                            "atau cari by tag aset (<i>“siapa pakai LT-…”</i>).</div>"}
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
            found = self.env["it_asset.asset"].search_read(
                self._asset_domain_for(kw), ["id", "name", "asset_tag"],
                limit=3)
            if not found:
                return {"miss": True, "hint": ""}
            a = found[0]
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

    def _tool_handover_list(self, _text):
        rows = self.env["it_asset.handover"].search_read(
            [], ["name", "asset_id", "receiver_id", "handover_date", "state"],
            limit=10, order="handover_date desc")
        if not rows:
            return {"miss": True,
                    "hint": "<div class='ai-foot'>Belum ada data handover (BAST).</div>"}
        parts = ["🤝 <b>Handover terakhir:</b>"
                 "<div class='ai-table-wrap'><table class='ai-table'>"
                 "<thead><tr><th>Ref</th><th>Aset → Penerima</th><th>Tgl</th></tr>"
                 "</thead><tbody>"]
        for h in rows:
            parts.append("<tr><td>%s</td><td><b>%s</b><div class='ai-sub'>→ %s</div></td><td>%s</td></tr>" % (
                _esc(h.get("name") or "-"),
                _esc(_m2o(h.get("asset_id")) or "-"),
                _esc(_m2o(h.get("receiver_id")) or "-"),
                _esc(h.get("handover_date") or "-")))
        parts.append("</tbody></table></div>")
        return {"html": "".join(parts), "tool": "handover_list"}

    def _tool_damage_list(self, _text):
        rows = self.env["it_asset.damage_report"].search_read(
            [], ["name", "asset_id", "damage_type", "report_date", "state"],
            limit=10, order="report_date desc")
        if not rows:
            return {"html": "Belum ada <b>damage report</b>. Kabar baik — "
                            "tidak ada laporan kerusakan tercatat 🎉",
                    "tool": "damage_list"}
        parts = ["📝 <b>Damage report terakhir:</b>"
                 "<div class='ai-table-wrap'><table class='ai-table'>"
                 "<thead><tr><th>Ref</th><th>Aset</th><th>Status</th></tr></thead><tbody>"]
        for d in rows:
            parts.append("<tr><td>%s<div class='ai-sub'>%s • %s</div></td><td><b>%s</b></td>"
                         "<td><span class='ai-badge info'>%s</span></td></tr>" % (
                             _esc(d.get("name") or "-"),
                             _esc(d.get("damage_type") or "-"),
                             _esc(d.get("report_date") or "-"),
                             _esc(_m2o(d.get("asset_id")) or "-"),
                             _esc(d.get("state") or "-")))
        parts.append("</tbody></table></div>")
        return {"html": "".join(parts), "tool": "damage_list"}

    def _tool_request_status(self, _text):
        mat = self.env["it_asset.material_request"].search_read(
            [], ["name", "employee_id", "request_date", "state"], limit=5,
            order="request_date desc")
        ass = self.env["it_asset.request"].search_read(
            [], ["name", "employee_id", "request_date", "state"], limit=5,
            order="request_date desc")
        parts = ["📥 <b>Status request terakhir:</b>"]
        for title, rows in (("Material Request", mat), ("Asset Request", ass)):
            if not rows:
                parts.append("<div class='ai-sub'>%s: belum ada data.</div>" % title)
                continue
            parts.append("<div class='ai-sec'>%s</div>"
                         "<div class='ai-table-wrap'><table class='ai-table'><tbody>" % title)
            for r in rows:
                parts.append("<tr><td><b>%s</b><div class='ai-sub'>%s • %s</div></td>"
                             "<td><span class='ai-badge info'>%s</span></td></tr>" % (
                                 _esc(r.get("name") or "-"),
                                 _esc(_m2o(r.get("employee_id")) or "-"),
                                 _esc(r.get("request_date") or "-"),
                                 _esc(r.get("state") or "-")))
            parts.append("</tbody></table></div>")
        return {"html": "".join(parts), "tool": "request_status"}

    def _tool_human_agent(self, text):
        return {"html": nlu.handover_reply(text)
                + "<div class='ai-foot'>Atau buat tiket via form IT yang sesuai "
                  "(Damage Report / Material Request).</div>",
                "tool": "handover_to_staff"}

    # Peta intent → tool (cermin routeDecision/defaultToolForIntent WACS).
    _TOOLS = {
        nlu.INTENT_RECAP: _tool_recap,
        nlu.INTENT_CHECK_STOCK: _tool_check_stock,
        nlu.INTENT_ASSET_SEARCH: _tool_asset_search,
        nlu.INTENT_ASSET_DETAIL: _tool_asset_detail,
        nlu.INTENT_ASSET_USER: _tool_asset_user,
        nlu.INTENT_ASSET_HISTORY: _tool_asset_history,
        nlu.INTENT_MAINTENANCE_LIST: _tool_maintenance_list,
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
