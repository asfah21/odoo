from odoo import models, fields, api

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    it_domain = fields.Selection([
        ('it', 'IT'),
        ('operation', 'Operation'),
    ], string='IT Domain', default='it', tracking=True)

    it_type = fields.Selection([
        ('asset', 'Asset'),
        ('accessory', 'Accessory'),
        ('spare_part', 'Spare Part'),
        ('tool', 'Tool'),
        ('consumable', 'Consumable'),
    ], string='IT Classification', default='asset', tracking=True)
