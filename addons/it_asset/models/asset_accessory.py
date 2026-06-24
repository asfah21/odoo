from odoo import models, fields


class ITAssetAccessory(models.Model):
    _name = 'it_asset.accessory'
    _description = 'Asset Accessory / Perintilan'
    _order = 'id asc'

    name = fields.Char(string='Accessory Name', required=True)
    asset_id = fields.Many2one('it_asset.asset', string='Asset', required=True, ondelete='cascade')
    quantity = fields.Float(string='Quantity', default=1.0, required=True)
    condition = fields.Selection([
        ('good', 'Good'),
        ('degraded', 'Degraded'),
        ('broken', 'Broken'),
    ], string='Condition', default='good', required=True)
    serial_number = fields.Char(string='Serial / Part Number')
