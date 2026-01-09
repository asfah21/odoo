from odoo import models, fields

class ITAssetMaintenance(models.Model):
    _name = 'it_asset.maintenance'
    _description = 'IT Asset Maintenance'
    _order = 'maintenance_date desc'

    asset_id = fields.Many2one(
        'it_asset.asset',
        string='Asset',
        required=True,
        ondelete='cascade'
    )
    maintenance_date = fields.Date(
        string='Maintenance Date',
        default=fields.Date.context_today
    )
    maintenance_type = fields.Selection([
        ('repair', 'Repair'),
        ('preventive', 'Preventive Maintenance'),
        ('upgrade', 'Upgrade'),
        ('other', 'Other')
    ], string='Type', default='repair', required=True)
    description = fields.Text(string='Description', required=True)
    cost = fields.Float(string='Cost')
    technician = fields.Char(string='Technician/Vendor')
