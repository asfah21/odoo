# -*- coding: utf-8 -*-
"""Riwayat chat Ask AI — sesi + pesan per user, retensi 10 hari.

Desain:
- Frontend (OWL) tidak lagi menyimpan riwayat dummy di memori; semua sesi
  dibaca/ditulis lewat RPC model ``it_asset.ask_ai`` (lihat ask_ai.py).
- Retensi: cron harian menghapus sesi yang tidak aktif > N hari
  (default 10, via System Parameter ``it_asset.ask_ai.history_retention_days``).
  Pesan ikut terhapus via ondelete cascade — tidak ada data yatim.
"""

from datetime import timedelta

from odoo import api, fields, models  # type: ignore[import-not-found]  # odoo tersedia di runtime Docker, bukan di editor lokal

RETENTION_PARAM = "it_asset.ask_ai.history_retention_days"
RETENTION_DEFAULT_DAYS = 10


class ITAskAISession(models.Model):
    _name = "it_asset.ask_ai.session"
    _description = "Ask AI Chat Session"
    _order = "last_seen desc, id desc"

    name = fields.Char(string="Judul", required=True, default="Percakapan baru")
    user_id = fields.Many2one(
        "res.users", string="Pengguna", required=True,
        default=lambda self: self.env.user.id, index=True)
    last_seen = fields.Datetime(
        string="Terakhir Aktif", default=fields.Datetime.now,
        required=True, index=True)
    message_ids = fields.One2many(
        "it_asset.ask_ai.message", "session_id", string="Pesan")
    message_count = fields.Integer(
        string="Jumlah Pesan", compute="_compute_message_count", store=True)
    # Lanjutan tertunda: hasil multi-kandidat menunggu konfirmasi user
    # ("balas tag/SN spesifik" atau "semua"). Dibaca pesan berikutnya,
    # diganti tiap ada hasil multi baru, dibersihkan tiap jawaban biasa.
    pending_action = fields.Char(string="Aksi Tertunda", readonly=True)
    pending_ids = fields.Char(string="ID Aset Tertunda (csv)", readonly=True)
    pending_label = fields.Char(string="Label Tertunda", readonly=True)
    # Ingatan topik: kategori/domain terakhir agar "yang rusak?" setelah
    # "stok cctv" dibaca sebagai CCTV rusak. Kedaluwarsa 60 menit.
    last_category = fields.Char(string="Kategori Terakhir", readonly=True)
    last_asset_type = fields.Char(string="Domain Terakhir", readonly=True)
    last_radio_kind = fields.Char(string="Jenis Radio Terakhir", readonly=True)
    last_ctx_at = fields.Datetime(string="Konteks Diperbarui", readonly=True)

    @api.depends("message_ids")
    def _compute_message_count(self):
        for rec in self:
            rec.message_count = len(rec.message_ids)

    @api.model
    def _retention_days(self):
        try:
            days = int(self.env["ir.config_parameter"].sudo().get_param(
                RETENTION_PARAM, RETENTION_DEFAULT_DAYS))
        except (TypeError, ValueError):
            days = RETENTION_DEFAULT_DAYS
        return max(days, 1)

    @api.model
    def _cron_cleanup_history(self):
        """Hapus sesi yang tidak aktif lebih dari N hari (default 10)."""
        days = self._retention_days()
        cutoff = fields.Datetime.now() - timedelta(days=days)
        old = self.search([("last_seen", "<", cutoff)])
        if old:
            old.unlink()
        return True

    def _touch(self):
        self.write({"last_seen": fields.Datetime.now()})


class ITAskAIMessage(models.Model):
    _name = "it_asset.ask_ai.message"
    _description = "Ask AI Chat Message"
    _order = "id asc"

    session_id = fields.Many2one(
        "it_asset.ask_ai.session", string="Sesi", required=True,
        ondelete="cascade", index=True)
    user_id = fields.Many2one(
        "res.users", string="Pengguna", required=True,
        default=lambda self: self.env.user.id, index=True)
    role = fields.Selection(
        [("user", "User"), ("ai", "AI")], string="Peran",
        required=True, default="user", index=True)
    body_html = fields.Text(string="Isi", required=True)
    intent = fields.Char(string="Intent")
    confidence = fields.Float(string="Confidence", digits=(3, 3))
