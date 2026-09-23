# -*- coding: utf-8 -*-
"""Tes isolasi sesi Ask AI + ACL setting.

Hanya jalan di dalam Odoo (``-u it_asset --test-enable``). File ini
sengaja tidak di-import dari ``tests/__init__.py`` saat ``odoo`` tidak
ada, supaya self-test NLU stdlib tetap bisa dijalankan di luar container.
"""

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "it_asset")
class TestAskAISessionIsolation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        user_group = cls.env.ref("base.group_user")
        cls.user_a = Users.create({
            "name": "Ask AI A",
            "login": "ask_ai_iso_a",
            "groups_id": [(6, 0, [user_group.id])],
        })
        cls.user_b = Users.create({
            "name": "Ask AI B",
            "login": "ask_ai_iso_b",
            "groups_id": [(6, 0, [user_group.id])],
        })

    def test_session_list_hides_other_user(self):
        Ask = self.env["it_asset.ask_ai"]
        sid = Ask.with_user(self.user_a).session_create(name="Milik A")["id"]
        rows = Ask.with_user(self.user_b).session_list()
        self.assertFalse(any(r["id"] == sid for r in rows))
        own = Ask.with_user(self.user_a).session_list()
        self.assertTrue(any(r["id"] == sid for r in own))

    def test_orm_search_cannot_read_foreign_session(self):
        Session = self.env["it_asset.ask_ai.session"]
        rec = Session.with_user(self.user_a).create({"name": "Rahasia A"})
        found = Session.with_user(self.user_b).search([("id", "=", rec.id)])
        self.assertFalse(found)

    def test_internal_user_cannot_write_setting(self):
        Setting = self.env["it_asset.ask_ai.setting"]
        rec = Setting.search([], limit=1)
        if not rec:
            self.skipTest("singleton Ask AI Setting belum ada")
        with self.assertRaises(AccessError):
            rec.with_user(self.user_a).write({"history_retention_days": 3})
