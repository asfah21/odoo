from odoo import models, fields, api

class ITAssetSwap(models.Model):
    _name = 'it_asset.swap'
    _description = 'IT Asset Swap History'
    _order = 'assignment_date desc'

    asset_id = fields.Many2one(
        'it_asset.asset',
        string='Asset',
        required=True,
        ondelete='cascade'
    )
    unit_id = fields.Many2one(
        'it_asset.unit',
        string='Fleet Unit',
        required=True
    )
    assignment_date = fields.Date(
        string='Assignment Date',
        default=fields.Date.context_today
    )
    return_date = fields.Date(string='Return Date')
    notes = fields.Text(string='Notes')
    state = fields.Selection([
        ('active', 'Active'),
        ('returned', 'Returned')
    ], string='Status', default='active')

    def action_return(self):
        for record in self:
            record.write({
                'return_date': fields.Date.context_today(record),
                'state': 'returned'
            })
            if record.asset_id.unit_id == record.unit_id:
                record.asset_id.write({
                    'unit_id': False,
                    'state': 'available'
                })
