from odoo import models, fields

class ITAssetUnit(models.Model):
    _name = 'it_asset.unit'
    _description = 'Operation Unit'
    _order = 'name'

    name = fields.Char(string='Unit Name', required=True, help="e.g. EX-01, DT-05")
    code = fields.Char(string='Code')
    description = fields.Text(string='Description')
    active = fields.Boolean(default=True)
    asset_ids = fields.One2many('it_asset.asset', 'unit_id', string='Installed Assets')

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Unit name must be unique!')
    ]
