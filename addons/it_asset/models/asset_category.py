from odoo import models, fields

class ITAssetCategory(models.Model):
    _name = 'it_asset.category'
    _description = 'IT Asset Category'

    name = fields.Char(string='Category Name', required=True)
    description = fields.Text(string='Description')
    color = fields.Integer(string='Color')
