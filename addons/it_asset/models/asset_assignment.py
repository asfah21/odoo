from odoo import models, fields, api

class ITAssetAssignment(models.Model):
    _name = 'it_asset.assignment'
    _description = 'IT Asset Assignment History'
    _order = 'assignment_date desc'

    asset_id = fields.Many2one(
        'it_asset.asset',
        string='Asset',
        required=True,
        ondelete='cascade'
    )
    employee_id = fields.Many2one(
        'hr.employee',
        string='Employee',
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
            if record.asset_id.employee_id == record.employee_id:
                record.asset_id.write({
                    'employee_id': False,
                    'state': 'available'
                })
