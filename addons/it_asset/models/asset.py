from odoo import models, fields, api

class ITAsset(models.Model):
    _name = 'it_asset.asset'
    _description = 'IT Asset'

    name = fields.Char(string='Asset Name', required=True)
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True,
        ondelete='restrict'
    )
    asset_tag = fields.Char(string='Asset Tag', required=True)
    category_id = fields.Many2one(
        'it_asset.category',
        string='Category'
    )
    lot_id = fields.Many2one(
        'stock.lot',
        string='Serial Number'
    )
    employee_id = fields.Many2one(
        'hr.employee',
        string='Assigned To'
    )
    state = fields.Selection(
        [
            ('available', 'Available'),
            ('assigned', 'Assigned'),
            ('repair', 'Under Repair'),
            ('retired', 'Retired'),
        ],
        string='Status',
        default='available'
    )
    assignment_ids = fields.One2many(
        'it_asset.assignment',
        'asset_id',
        string='Assignments'
    )
    maintenance_ids = fields.One2many(
        'it_asset.maintenance',
        'asset_id',
        string='Maintenances'
    )

    _sql_constraints = [
        (
            'unique_lot_id',
            'unique(lot_id)',
            'This serial number is already assigned to another asset.'
        )
    ]

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            return {
                'domain': {
                    'lot_id': [('product_id', '=', self.product_id.id)]
                }
            }
        return {'domain': {'lot_id': []}}

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        for record in self:
            if record.state in ('repair', 'retired'):
                continue
            if record.employee_id:
                record.state = 'assigned'
            else:
                record.state = 'available'
