# -*- coding: utf-8 -*-
"""Kamus otomatis Ask AI — snapshot ringan dari database untuk NLU.

Ide (sesuai permintaan user):
- Tombol **Generate** di menu Ask AI -> Setting membaca database
  (produk, kategori, harga, stok, keterangan, aset, unit, karyawan)
  lalu menyimpannya sebagai baris kamus ``it_asset.ask_ai.term``.
- Saat user bertanya (walau typo), backend mencocokkan kata ke kamus
  secara lokal (tanpa token API, tanpa memanggil Qwen) sehingga
  intent/classifier cepat paham konteks.
- Qwen lokal HANYA dipakai untuk rephrase jawaban agar tidak robotik
  (lihat ``ask_ai.py``). Tanpa Qwen pun chatbot tetap jalan.

Desain:
- Satu baris = satu istilah kanonik + bentuk normal + ringkasan info
  (kategori, harga, stok, keterangan) untuk tooltip/debug.
- Generate bersifat idempoten: hapus + isi ulang per ``source_model``,
  atau full rebuild bila diminta.
- Normalisasi memakai fungsi yang sama dengan NLU (lowercase, lipat
  spasi/tanda baca) agar pencocokan konsisten.
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
    _description = "Ask AI Dictionary (Kamus Otomatis)"
    _order = "kind asc, term asc"
    _rec_name = "term"

    term = fields.Char(string="Istilah", required=True, index=True)
    normalized = fields.Char(string="Normalisasi", index=True,
                             help="Bentuk normal untuk pencocokan typo-tolerant.")
    kind = fields.Selection([
        ("product", "Produk"),
        ("category", "Kategori Aset"),
        ("unit_category", "Kategori Unit"),
        ("asset", "Aset (tag/nama)"),
        ("consumable", "Consumable"),
        ("unit", "Unit/Fleet"),
        ("employee", "Karyawan"),
        ("alias", "Alias Lapangan"),
    ], string="Jenis", required=True, default="product", index=True)
    category = fields.Char(string="Kategori")
    price = fields.Float(string="Harga (List)", digits="Product Price")
    cost = fields.Float(string="Cost (Standard)", digits="Product Price")
    qty = fields.Float(string="Stok/Qty")
    uom = fields.Char(string="Satuan")
    description = fields.Text(string="Keterangan")
    source_model = fields.Char(string="Model Sumber", index=True)
    source_id = fields.Integer(string="ID Sumber")
    active = fields.Boolean(default=True)
    usage_count = fields.Integer(string="Dipakai", default=0, readonly=True,
                                 help="Berapa kali istilah ini membantu koreksi typo.")

    _sql_constraints = [
        ("term_kind_uniq", "unique(term, kind, source_model, source_id)",
         "Istilah kamus duplikat!"),
    ]

    @api.model
    def _upsert(self, vals_list, batch=500):
        """Insert ringan dengan deduplication di Python (hindari error unik)."""
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
            except Exception as exc:  # satu chunk gagal -> coba satu-satu
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
        """Cari kandidat kamus untuk satu kata/frasa (ilike, ringan)."""
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
