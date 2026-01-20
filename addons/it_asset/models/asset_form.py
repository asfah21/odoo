from odoo import models, fields, api, _

class ITAssetRequest(models.Model):
    _name = 'it_asset.request'
    _description = 'Asset Request Form'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    employee_id = fields.Many2one('hr.employee', string='Requester', required=True, default=lambda self: self.env.user.employee_id)
    department_id = fields.Many2one('hr.department', string='Department', related='employee_id.department_id', readonly=True)
    category_id = fields.Many2one('it_asset.category', string='Asset Category', required=True)
    request_date = fields.Date(string='Request Date', default=fields.Date.context_today, required=True)
    reason = fields.Text(string='Reason for Request')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('fulfilled', 'Fulfilled'),
        ('rejected', 'Rejected')
    ], string='Status', default='draft', tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('it_asset.request') or _('New')
        return super().create(vals_list)

    def action_submit(self):
        self.write({'state': 'submitted'})

    def action_approve(self):
        self.write({'state': 'approved'})

    def action_reject(self):
        self.write({'state': 'rejected'})

    def action_fulfill(self):
        self.write({'state': 'fulfilled'})


class ITAssetHandover(models.Model):
    _name = 'it_asset.handover'
    _description = 'Asset Handover Form'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'handover_date desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    asset_id = fields.Many2one('it_asset.asset', string='Asset', required=True)
    sender_id = fields.Many2one('hr.employee', string='Sent By', required=True)
    receiver_id = fields.Many2one('hr.employee', string='Received By', required=True)
    handover_date = fields.Date(string='Handover Date', default=fields.Date.context_today)
    notes = fields.Text(string='Notes')
    signature = fields.Binary(string='Receiver Signature', help='Signature of the person receiving the asset')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('signed', 'Signed')
    ], string='Status', default='draft', tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('it_asset.handover') or _('New')
        return super().create(vals_list)

    def action_sign(self):
        self.write({'state': 'signed'})


class ITAssetDamageReport(models.Model):
    _name = 'it_asset.damage_report'
    _description = 'Asset Damage Report'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'report_date desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    asset_id = fields.Many2one('it_asset.asset', string='Asset', required=True)
    employee_id = fields.Many2one('hr.employee', string='Reported By', required=True, default=lambda self: self.env.user.employee_id)
    report_date = fields.Date(string='Report Date', default=fields.Date.context_today)
    damage_type = fields.Selection([
        ('physical', 'Physical Damage'),
        ('system', 'System/Software Issue'),
        ('lost', 'Lost'),
        ('other', 'Other')
    ], string='Damage Type', required=True)
    description = fields.Text(string='Description of Damage', required=True)
    action_taken = fields.Text(string='Action Taken')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('resolved', 'Resolved')
    ], string='Status', default='draft', tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                seq = self.env['ir.sequence'].next_by_code('it_asset.damage_report') or _('New')
                # Custom Roman Month Logic
                # Check if seq is a raw number (or at least doesn't contain the full old suffix already)
                # We assume standard upgrade flow where seq becomes '0001'
                if seq != _('New'):
                    # Default todays date if not provided
                    report_date = vals.get('report_date')
                    if report_date:
                        report_date = fields.Date.from_string(report_date)
                    else:
                        report_date = fields.Date.today()
                        
                    year = report_date.year
                    month = report_date.month
                    roman_map = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V', 6: 'VI', 
                                 7: 'VII', 8: 'VIII', 9: 'IX', 10: 'X', 11: 'XI', 12: 'XII'}
                    roman_month = roman_map.get(month, 'I')
                    
                    # Prevent double formatting if XML didn't update (Basic check: count slashes)
                    if seq.count('/') < 2:
                        vals['name'] = f"{seq}/{roman_month}/BA/GSI-IT/{year}"
                    else:
                        vals['name'] = seq
        return super().create(vals_list)

    def action_confirm(self):
        self.write({'state': 'confirmed'})
        if self.asset_id:
            self.asset_id.write({'condition': 'broken'})

    def action_resolve(self):
        self.write({'state': 'resolved'})
