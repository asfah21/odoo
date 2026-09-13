# -*- coding: utf-8 -*-
"""Ask AI Setting — IT -> Ask AI -> Setting menu.

One screen for:
- Qwen Decide (intent) + Qwen Rephrase (final answer) configuration.
  Design: a local dictionary that understands context (including typos),
  Qwen ONLY rephrases so answers sound less robotic.
- A **Generate Dictionary** button that reads the database (products,
  categories, prices, stock, descriptions, assets, units, employees)
  into an auto dictionary.
- Qwen connection test + term count + last generate time.

All values are also mirrored to ``ir.config_parameter`` for compatibility
with legacy code reading ``it_asset.ask_ai.*`` directly:
  - it_asset.ask_ai.llm_enabled / llm_url / llm_model / llm_timeout / llm_json_mode
  - it_asset.ask_ai.rephrase_enabled / rephrase_max_tokens / rephrase_temperature
  - it_asset.ask_ai.history_retention_days
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Model default Qwen 0.6B (rephrase-only). File GGUF Q4_K_M ~397 MB dari:
#   unsloth/Qwen3-0.6B-GGUF  (file: Qwen3-0.6B-Q4_K_M.gguf)
# Kompatibel llama-server OpenAI-compatible di port 8081.
# Alternatif SEA/Indonesia: sail/Sailor-0.5B-Chat-gguf
# (file: ggml-model-Q4_K_M.gguf) — lihat llm/README.md.
QWEN_REPO = "unsloth/Qwen3-0.6B-GGUF"
QWEN_FILE = "Qwen3-0.6B-Q4_K_M.gguf"
QWEN_SAILOR_REPO = "sail/Sailor-0.5B-Chat-gguf"
QWEN_SAILOR_FILE = "ggml-model-Q4_K_M.gguf"

_PARAMS = {
    "llm_enabled": ("it_asset.ask_ai.llm_enabled", "False"),
    "llm_url": ("it_asset.ask_ai.llm_url", "http://127.0.0.1:8081"),
    "llm_model": ("it_asset.ask_ai.llm_model", "qwen3-0.6b"),
    "llm_timeout": ("it_asset.ask_ai.llm_timeout", "10"),
    "llm_json_mode": ("it_asset.ask_ai.llm_json_mode", "True"),
    "rephrase_enabled": ("it_asset.ask_ai.rephrase_enabled", "False"),
    "rephrase_max_tokens": ("it_asset.ask_ai.rephrase_max_tokens", "150"),
    "rephrase_temperature": ("it_asset.ask_ai.rephrase_temperature", "0.7"),
    "history_retention_days": ("it_asset.ask_ai.history_retention_days", "10"),
    "dict_auto_note": ("it_asset.ask_ai.dict_note", ""),
}


class ITAskAISetting(models.Model):
    _name = "it_asset.ask_ai.setting"
    _description = "Ask AI Setting (Dictionary + Qwen Rephrase)"

    name = fields.Char(default="Ask AI Setting", readonly=True)
    # --- Qwen Decide (optional, L1 intent) ---
    llm_enabled = fields.Boolean(string="Enable Qwen Decide (intent)")
    llm_url = fields.Char(string="llama-server URL", default="http://127.0.0.1:8081")
    llm_model = fields.Char(string="Decide Model", default="qwen3-0.6b")
    llm_timeout = fields.Float(string="Decide Timeout (seconds)", default=10.0)
    llm_json_mode = fields.Boolean(string="JSON Mode", default=True)
    # --- Qwen Rephrase (recommended: 0.6B, wording polish only) ---
    rephrase_enabled = fields.Boolean(
        string="Enable Qwen Rephrase (0.6B)",
        help="If enabled, factual answers from the database are polished by Qwen "
             "to sound natural. Facts/numbers/names are untouched. "
             "On failure the original answer is used.")
    rephrase_max_tokens = fields.Integer(string="Rephrase Max Tokens", default=150)
    rephrase_temperature = fields.Float(string="Rephrase Temperature", default=0.7)
    # --- Retention ---
    history_retention_days = fields.Integer(string="History Retention (days)", default=10)
    # --- Dictionary ---
    dict_count = fields.Integer(string="Dictionary Term Count",
                                compute="_compute_dict_info")
    dict_last_generate = fields.Datetime(string="Last Generated", readonly=True)
    dict_last_summary = fields.Text(string="Last Generate Summary",
                                    readonly=True)
    qwen_status = fields.Char(string="Qwen Status", readonly=True,
                              help="Last connection test result.")

    @api.depends()
    def _compute_dict_info(self):
        for rec in self:
            try:
                rec.dict_count = self.env["it_asset.ask_ai.term"].search_count([])
            except Exception:
                rec.dict_count = 0

    # ------------------------------------------------------------------
    # singleton + sinkronisasi ir.config_parameter
    # ------------------------------------------------------------------
    @api.model
    def _get_singleton(self):
        rec = self.search([], limit=1)
        if not rec:
            rec = self.create({})
            rec._load_from_params()
        return rec

    def _load_from_params(self):
        Param = self.env["ir.config_parameter"].sudo()
        def _bool(key, default):
            return str(Param.get_param(key, default)).strip().lower() in (
                "1", "true", "yes")
        for rec in self:
            rec.llm_enabled = _bool(*_PARAMS["llm_enabled"])
            rec.llm_url = Param.get_param(*_PARAMS["llm_url"])
            rec.llm_model = Param.get_param(*_PARAMS["llm_model"])
            try:
                rec.llm_timeout = float(Param.get_param(*_PARAMS["llm_timeout"]))
            except (TypeError, ValueError):
                rec.llm_timeout = 10.0
            rec.llm_json_mode = _bool(*_PARAMS["llm_json_mode"])
            rec.rephrase_enabled = _bool(*_PARAMS["rephrase_enabled"])
            try:
                rec.rephrase_max_tokens = int(Param.get_param(*_PARAMS["rephrase_max_tokens"]))
            except (TypeError, ValueError):
                rec.rephrase_max_tokens = 150
            try:
                rec.rephrase_temperature = float(Param.get_param(*_PARAMS["rephrase_temperature"]))
            except (TypeError, ValueError):
                rec.rephrase_temperature = 0.7
            try:
                rec.history_retention_days = int(Param.get_param(*_PARAMS["history_retention_days"]))
            except (TypeError, ValueError):
                rec.history_retention_days = 10

    def write(self, vals):
        res = super().write(vals)
        Param = self.env["ir.config_parameter"].sudo()
        mapping = {
            "llm_enabled": "it_asset.ask_ai.llm_enabled",
            "llm_url": "it_asset.ask_ai.llm_url",
            "llm_model": "it_asset.ask_ai.llm_model",
            "llm_timeout": "it_asset.ask_ai.llm_timeout",
            "llm_json_mode": "it_asset.ask_ai.llm_json_mode",
            "rephrase_enabled": "it_asset.ask_ai.rephrase_enabled",
            "rephrase_max_tokens": "it_asset.ask_ai.rephrase_max_tokens",
            "rephrase_temperature": "it_asset.ask_ai.rephrase_temperature",
            "history_retention_days": "it_asset.ask_ai.history_retention_days",
        }
        for rec in self:
            for field, key in mapping.items():
                if field in vals:
                    Param.set_param(key, str(vals[field]))
        return res

    # ------------------------------------------------------------------
    # GENERATE DICTIONARY — read database -> it_asset.ask_ai.term
    # ------------------------------------------------------------------
    def action_generate_dictionary(self):
        """Main button: read products, categories, prices, stock, notes, etc."""
        self.ensure_one()
        Term = self.env["it_asset.ask_ai.term"]
        counts = {}

        def _add(rows, kind):
            if rows:
                counts[kind] = Term._upsert(rows)

        # 1. Products (name, code, category, price, cost, notes)
        try:
            products = self.env["product.product"].search_read(
                [], ["name", "default_code", "categ_id", "list_price",
                     "standard_price", "uom_id", "description", "type"],
                limit=5000)
        except Exception as exc:
            _logger.warning("Ask AI dict: baca product gagal: %s", exc)
            products = []
        rows = []
        for p in products:
            name = (p.get("name") or "").strip()
            if not name:
                continue
            cat = p.get("categ_id")[1] if isinstance(p.get("categ_id"), (list, tuple)) else ""
            uom = p.get("uom_id")[1] if isinstance(p.get("uom_id"), (list, tuple)) else ""
            desc = (p.get("description") or "")[:500]
            rows.append({
                "term": name[:120], "kind": "product",
                "category": (cat or "")[:80],
                "price": p.get("list_price") or 0.0,
                "cost": p.get("standard_price") or 0.0,
                "uom": (uom or "")[:20], "description": desc,
                "source_model": "product.product", "source_id": p["id"],
            })
            code = (p.get("default_code") or "").strip()
            if code and code.lower() != name.lower():
                rows.append({
                    "term": code[:120], "kind": "product",
                    "category": (cat or "")[:80],
                    "price": p.get("list_price") or 0.0,
                    "cost": p.get("standard_price") or 0.0,
                    "uom": (uom or "")[:20], "description": "Code: %s (%s)" % (code, name[:60]),
                    "source_model": "product.product", "source_id": p["id"],
                })
        # drop the old product snapshot first to avoid pile-up, then refill
        Term.search([("kind", "=", "product")]).unlink()
        _add(rows, "product")

        # 2. IT asset categories
        try:
            cats = self.env["it_asset.category"].search_read(
                [], ["name", "description"], limit=500)
        except Exception:
            cats = []
        rows = [{"term": (c.get("name") or "").strip()[:80], "kind": "category",
                 "description": (c.get("description") or "")[:300],
                 "source_model": "it_asset.category", "source_id": c["id"]}
                for c in cats if (c.get("name") or "").strip()]
        Term.search([("kind", "=", "category")]).unlink()
        _add(rows, "category")

        # 3. Unit/fleet categories
        try:
            ucats = self.env["it_asset.unit.category"].search_read(
                [], ["name"], limit=100)
        except Exception:
            ucats = []
        rows = [{"term": (c.get("name") or "").strip()[:80], "kind": "unit_category",
                 "source_model": "it_asset.unit.category", "source_id": c["id"]}
                for c in ucats if (c.get("name") or "").strip()]
        Term.search([("kind", "=", "unit_category")]).unlink()
        _add(rows, "unit_category")

        # 4. Assets (tag + name + model + short spec)
        try:
            assets = self.env["it_asset.asset"].search_read(
                [], ["name", "asset_tag", "model", "category_id",
                     "product_id", "state", "condition", "specification"],
                limit=5000)
        except Exception as exc:
            _logger.warning("Ask AI dict: baca asset gagal: %s", exc)
            assets = []
        rows = []
        for a in assets:
            tag = (a.get("asset_tag") or "").strip()
            nm = (a.get("name") or "").strip()
            cat = a.get("category_id")[1] if isinstance(a.get("category_id"), (list, tuple)) else ""
            prod = a.get("product_id")[1] if isinstance(a.get("product_id"), (list, tuple)) else ""
            info = "%s | %s | %s" % (cat or "-", a.get("state") or "-", a.get("condition") or "-")
            if tag:
                rows.append({"term": tag[:80], "kind": "asset", "category": (cat or "")[:80],
                             "description": ("%s (%s)" % (nm[:60], info))[:300],
                             "source_model": "it_asset.asset", "source_id": a["id"]})
            if nm:
                rows.append({"term": nm[:120], "kind": "asset", "category": (cat or "")[:80],
                             "description": ("%s | produk: %s" % (info, prod[:60] if prod else "-"))[:300],
                             "source_model": "it_asset.asset", "source_id": a["id"]})
            mdl = (a.get("model") or "").strip()
            if mdl and mdl.lower() not in (nm.lower(), tag.lower()):
                rows.append({"term": mdl[:80], "kind": "asset", "category": (cat or "")[:80],
                             "description": "Model: %s (%s)" % (mdl[:40], nm[:40]),
                             "source_model": "it_asset.asset", "source_id": a["id"]})
        Term.search([("kind", "=", "asset")]).unlink()
        _add(rows, "asset")

        # 5. Consumables (name + qty + min + product price)
        try:
            cons = self.env["it_asset.consumable"].search_read(
                [], ["name", "product_id", "min_quantity", "description"],
                limit=2000)
            prod_price = {p["id"]: (p.get("list_price") or 0.0,
                                    p.get("standard_price") or 0.0)
                          for p in products if p.get("id")}
            prod_qty = {}
            try:
                for p in self.env["product.product"].browse(
                        [c["product_id"][0] for c in cons if c.get("product_id")]).read(
                        ["qty_available"]):
                    prod_qty[p["id"]] = p.get("qty_available") or 0.0
            except Exception:
                pass
        except Exception:
            cons, prod_price, prod_qty = [], {}, {}
        rows = []
        for c in cons:
            nm = (c.get("name") or "").strip()
            if not nm:
                continue
            pid = c["product_id"][0] if isinstance(c.get("product_id"), (list, tuple)) else 0
            price, cost = prod_price.get(pid, (0.0, 0.0))
            rows.append({
                "term": nm[:120], "kind": "consumable",
                "price": price, "cost": cost,
                "qty": prod_qty.get(pid, 0.0),
                "description": (("min %s. " % c.get("min_quantity") if c.get("min_quantity") else "")
                                + (c.get("description") or ""))[:300],
                "source_model": "it_asset.consumable", "source_id": c["id"],
            })
        Term.search([("kind", "=", "consumable")]).unlink()
        _add(rows, "consumable")

        # 6. Units/fleet (name + brand + model + status)
        try:
            units = self.env["it_asset.unit"].search_read(
                [], ["name", "category_id", "brand", "model", "state"],
                limit=1000)
        except Exception:
            units = []
        rows = []
        for u in units:
            nm = (u.get("name") or "").strip()
            if not nm:
                continue
            cat = u.get("category_id")[1] if isinstance(u.get("category_id"), (list, tuple)) else ""
            rows.append({
                "term": nm[:80], "kind": "unit", "category": (cat or "")[:80],
                "description": "%s %s (%s)" % (u.get("brand") or "-", u.get("model") or "-",
                                               u.get("state") or "-"),
                "source_model": "it_asset.unit", "source_id": u["id"],
            })
        Term.search([("kind", "=", "unit")]).unlink()
        _add(rows, "unit")

        # 7. Employees (for "who uses / whose is it")
        try:
            emps = self.env["hr.employee"].search_read(
                [], ["name"], limit=2000)
        except Exception:
            emps = []
        rows = [{"term": (e.get("name") or "").strip()[:80], "kind": "employee",
                 "source_model": "hr.employee", "source_id": e["id"]}
                for e in emps if (e.get("name") or "").strip()]
        Term.search([("kind", "=", "employee")]).unlink()
        _add(rows, "employee")

        total = sum(counts.values())
        summary = " | ".join("%s: %d" % (k, v) for k, v in sorted(counts.items()))
        self.write({
            "dict_last_generate": fields.Datetime.now(),
            "dict_last_summary": "Total %d terms (%s)" % (total, summary),
        })
        # notify back to the form
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Dictionary generated successfully",
                "message": "Total %d terms. %s" % (total, summary),
                "type": "success",
                "sticky": False,
            },
        }

    def action_clear_dictionary(self):
        self.ensure_one()
        self.env["it_asset.ask_ai.term"].search([]).unlink()
        self.write({"dict_last_summary": "Cleared manually."})
        return True

    def action_open_dictionary(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Ask AI Dictionary",
            "res_model": "it_asset.ask_ai.term",
            "view_mode": "list,form",
            "target": "current",
        }

    def action_test_qwen(self):
        """Ping llama-server (/v1/models). Failure = clear status, not an error."""
        self.ensure_one()
        import json as _json
        import urllib.request as _urlrequest
        url = (self.llm_url or "").rstrip("/") + "/v1/models"
        try:
            req = _urlrequest.Request(url, headers={"Content-Type": "application/json"})
            with _urlrequest.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            models = [m.get("id", "?") for m in data.get("data", [])][:3]
            status = "OK — %s" % (", ".join(models) if models else "connected")
        except Exception as exc:
            status = "FAILED — %s. Run: scripts/install-qwen.ps1 (Windows) / install-qwen.sh (Linux)." % exc
        self.write({"qwen_status": status})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": "Test Qwen", "message": status,
                       "type": "success" if status.startswith("OK") else "warning"},
        }

    @api.model
    def _cron_refresh_dictionary(self):
        """Optional: daily dictionary refresh (enable via Automated Actions if needed)."""
        rec = self.search([], limit=1)
        if rec:
            try:
                rec.action_generate_dictionary()
            except Exception as exc:
                _logger.warning("Ask AI dict auto-refresh gagal: %s", exc)
        return True
