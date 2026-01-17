from odoo import models, fields

class ITAssetUnit(models.Model):
    _name = 'it_asset.unit'
    _description = 'Operation Unit'
    _order = 'name'

    name = fields.Char(string='Unit Name', required=True, help="e.g. EX-01, DT-05")
    brand = fields.Char(string='Brand')
    model = fields.Char(string='Model')
    description = fields.Text(string='Description')
    active = fields.Boolean(default=True)
    asset_ids = fields.Many2many('it_asset.asset', 
                               compute='_compute_asset_ids', 
                               inverse='_inverse_asset_ids', 
                               string='Installed Assets', 
                               domain=[('asset_type', '=', 'operation'), ('state', '=', 'available')])

    def _compute_asset_ids(self):
        for unit in self:
            unit.asset_ids = self.env['it_asset.asset'].search([('unit_id', '=', unit.id)])

    def _inverse_asset_ids(self):
        for unit in self:
            # Get current assets in DB for this unit
            existing_assets = self.env['it_asset.asset'].search([('unit_id', '=', unit.id)])
            
            # Identify which should be added and which should be removed
            to_add = unit.asset_ids - existing_assets
            to_remove = existing_assets - unit.asset_ids
            
            # Assignments: this will trigger the stock moves via asset.py write()
            if to_add:
                to_add.write({'unit_id': unit.id})
            if to_remove:
                to_remove.write({'unit_id': False})

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Unit name must be unique!')
    ]
