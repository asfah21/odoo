# -*- coding: utf-8 -*-
"""Ask AI auto dictionary — lightweight database snapshot for NLU.

Design (per user request):
- The **Generate** button in Ask AI -> Setting reads the database
  (products, categories, prices, stock, descriptions, assets, units,
  employees) and stores them as ``it_asset.ask_ai.term`` rows.
- When a user asks (even with typos), the backend matches words against
  the dictionary locally (no API tokens, no Qwen call) so the
  intent/classifier quickly grasps the context.
- Local Qwen is ONLY used to rephrase answers so they sound less robotic
  (see ``ask_ai.py``). The chatbot works even without Qwen.

Design:
- One row = one canonical term + normalized form + info summary
  (category, price, stock, description) for tooltip/debug.
- Generate is idempotent: delete + refill per ``source_model``,
  or full rebuild when requested.
- Normalization uses the same function as NLU (lowercase, fold
  spaces/punctuation) for consistent matching.
"""

import logging
import re as _re

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


def _norm(text):
    """Normalisasi ringan: lowercase + hanya alnum/spasi + rapikan spasi."""
    s = (text or "").lower()
    s = _re.sub(r"[^a-z0-9\s]", " ", s)
    return " ".join(s.split())


class ITAskAIDictionary(models.Model):
    _name = "it_asset.ask_ai.term"
    _description = "Ask AI Dictionary (Auto Dictionary)"
    _order = "kind asc, term asc"
    _rec_name = "term"

    term = fields.Char(string="Term", required=True, index=True)
    normalized = fields.Char(string="Normalized", index=True,
                             help="Normalized form for typo-tolerant matching.")
    kind = fields.Selection([
        ("product", "Product"),
        ("category", "Asset Category"),
        ("unit_category", "Unit Category"),
        ("asset", "Asset (tag/name)"),
        ("consumable", "Consumable"),
        ("unit", "Unit/Fleet"),
        ("employee", "Employee"),
        ("alias", "Field Alias"),
    ], string="Type", required=True, default="product", index=True)
    category = fields.Char(string="Category")
    price = fields.Float(string="Price (List)", digits="Product Price")
    cost = fields.Float(string="Cost (Standard)", digits="Product Price")
    qty = fields.Float(string="Stock/Qty")
    uom = fields.Char(string="UoM")
    description = fields.Text(string="Description")
    source_model = fields.Char(string="Source Model", index=True)
    source_id = fields.Integer(string="Source ID")
    active = fields.Boolean(default=True)
    usage_count = fields.Integer(string="Usage Count", default=0, readonly=True,
                                 help="How many times this term helped fix a typo.")

    _sql_constraints = [
        ("term_kind_uniq", "unique(term, kind, source_model, source_id)",
         "Duplicate dictionary term!"),
    ]

    @api.model
    def _upsert(self, vals_list, batch=500):
        """Lightweight insert with Python-side dedup (avoids unique errors)."""
        seen = set()
        clean = []
        for v in vals_list:
            key = (v.get("term", ""), v.get("kind", ""),
                   v.get("source_model", ""), v.get("source_id", 0))
            if key in seen:
                continue
            seen.add(key)
            if not v.get("normalized"):
                v["normalized"] = _norm(v.get("term", ""))
            clean.append(v)
        created = 0
        for i in range(0, len(clean), batch):
            chunk = clean[i:i + batch]
            try:
                self.create(chunk)
                created += len(chunk)
            except Exception as exc:  # one chunk failed -> retry row by row
                _logger.warning("Ask AI dict chunk gagal, fallback per-baris: %s", exc)
                self.env.cr.rollback()
                for v in chunk:
                    try:
                        with self.env.cr.savepoint():
                            self.create(v)
                            created += 1
                    except Exception:
                        continue
        return created

    @api.model
    def lookup(self, text, limit=5):
        """Find dictionary candidates for one word/phrase (light ilike)."""
        text = (text or "").strip()
        if not text or len(text) < 2:
            return []
        return self.search_read(
            ["|", ("term", "ilike", text), ("normalized", "ilike", _norm(text))],
            ["term", "normalized", "kind", "category", "price", "qty",
             "uom", "description"],
            limit=limit, order="id asc")

    def action_bump_usage(self):
        for rec in self:
            rec.usage_count += 1
        return True

    @api.model
    def bump_terms(self, words):
        """Naikkan usage_count untuk term yang dipakai koreksi typo.

        Dipanggil dari Ask AI (sudo) agar user biasa tanpa write kamus
        tetap bisa menandai term yang berguna.
        """
        seen = set()
        bumped = self.browse()
        for raw in words or []:
            w = (raw or "").strip().lower()
            if not w or w in seen:
                continue
            seen.add(w)
            recs = self.search([("normalized", "=", _norm(w))], limit=8)
            if not recs:
                recs = self.search([("term", "=ilike", w)], limit=3)
            bumped |= recs
        for rec in bumped:
            rec.usage_count += 1
        return len(bumped)
