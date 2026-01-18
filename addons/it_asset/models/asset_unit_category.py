from odoo import models, fields, api

class ITAssetUnitCategory(models.Model):
    _name = 'it_asset.unit.category'
    _description = 'Fleet Category'
    _order = 'name'

    name = fields.Char(string='Category Name', required=True, help="e.g. Dump Truck, Excavator")
    code = fields.Char(string='Code')
    active = fields.Boolean(default=True)
    unit_ids = fields.One2many('it_asset.unit', 'category_id', string='Units')

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Category name must be unique!')
    ]

    @api.model
    def init_master_data(self, categories):
        """Helper to load data only if it doesn't exist by name"""
        for vals in categories:
            if not self.search([('name', '=', vals.get('name'))], limit=1):
                self.create(vals)
