from odoo import models, fields, api, _


class ITAssetMaterialRequest(models.Model):
    _name = 'it_asset.material_request'
    _description = 'Material Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    employee_id = fields.Many2one('hr.employee', string='Requester', required=True, default=lambda self: self.env.user.employee_id)
    department_id = fields.Many2one('hr.department', string='Department', related='employee_id.department_id', readonly=True)
    request_date = fields.Date(string='Request Date', default=fields.Date.context_today, required=True)
    reason = fields.Text(string='Reason / Notes')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('fulfilled', 'Fulfilled'),
        ('rejected', 'Rejected')
    ], string='Status', default='draft', tracking=True)

    line_ids = fields.One2many('it_asset.material_request.line', 'request_id', string='Items')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('it_asset.material_request') or _('New')
        return super().create(vals_list)

    def action_submit(self):
        self.write({'state': 'submitted'})

    def action_approve(self):
        self.write({'state': 'approved'})

    def action_reject(self):
        self.write({'state': 'rejected'})

    def action_fulfill(self):
        self.write({'state': 'fulfilled'})


class ITAssetMaterialRequestLine(models.Model):
    _name = 'it_asset.material_request.line'
    _description = 'Material Request Line'
    _order = 'id asc'

    request_id = fields.Many2one('it_asset.material_request', string='Request', required=True, ondelete='cascade')
    name = fields.Char(string='Item Name', required=True)
    quantity = fields.Float(string='Quantity', default=1.0, required=True)
    uom = fields.Char(string='Unit of Measure', default='Unit')
    notes = fields.Text(string='Notes')
