from odoo import models, fields


class ITAssetHandoverAccessory(models.Model):
    _name = 'it_asset.handover.accessory'
    _description = 'Handover Accessory / Perintilan'
    _order = 'id asc'

    handover_id = fields.Many2one('it_asset.handover', string='Handover', required=True, ondelete='cascade')
    name = fields.Char(string='Accessory Name', required=True)
    quantity = fields.Float(string='Quantity', default=1.0, required=True)
    condition = fields.Selection([
        ('good', 'Good'),
        ('degraded', 'Degraded'),
        ('broken', 'Broken'),
    ], string='Condition', default='good', required=True)
    serial_number = fields.Char(string='Serial / Part Number')
