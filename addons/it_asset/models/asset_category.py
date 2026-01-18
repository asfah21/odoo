from odoo import models, fields, api

class ITAssetCategory(models.Model):
    _name = 'it_asset.category'
    _description = 'IT Asset Category'

    name = fields.Char(string='Category Name', required=True)
    description = fields.Text(string='Description')
    color = fields.Integer(string='Color')
    is_consumable = fields.Boolean(string='Is Consumable', default=False)

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Category name must be unique!')
    ]

    @api.model
    def init_master_data(self, categories):
        """Helper to load data only if it doesn't exist by name"""
        for vals in categories:
            if not self.search([('name', '=', vals.get('name'))], limit=1):
                self.create(vals)
