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
    priority = fields.Selection([
        ('p1', 'P1'),
        ('p2', 'P2'),
        ('p3', 'P3'),
    ], string='Priority', default='p2')
    reason = fields.Text(string='Reason')
    notes = fields.Text(string='Notes')
    checked_by_id = fields.Many2one('hr.employee', string='Diperiksa Oleh')
    verified_by_id = fields.Many2one('hr.employee', string='Diverifikasi Oleh')
    known_by_id = fields.Many2one('hr.employee', string='Diketahui Oleh')
    approved_by_id = fields.Many2one('hr.employee', string='Disetujui Oleh')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('partially_fulfilled', 'Partially Fulfilled'),
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
        self.ensure_one()
        wizard_lines = []
        for line in self.line_ids:
            remaining = max(0.0, line.quantity - line.qty_fulfilled)
            if remaining > 0:
                wizard_lines.append((0, 0, {
                    'line_id': line.id,
                    'name': line.name,
                    'uom': line.uom,
                    'qty_requested': line.quantity,
                    'qty_previously_fulfilled': line.qty_fulfilled,
                    'qty_remaining': remaining,
                    'qty_to_fulfill': remaining,
                }))
        if not wizard_lines:
            raise UserError(_("All items in this request have already been fulfilled."))

        wizard = self.env['it_asset.material_request.fulfill.wizard'].create({
            'request_id': self.id,
            'line_ids': wizard_lines,
        })
        return {
            'name': _('Fulfill Material Request'),
            'type': 'ir.actions.act_window',
            'res_model': 'it_asset.material_request.fulfill.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _check_fulfillment_status(self):
        for rec in self:
            if not rec.line_ids:
                continue
            total_qty = sum(rec.line_ids.mapped('quantity'))
            total_fulfilled = sum(rec.line_ids.mapped('qty_fulfilled'))
            if total_fulfilled >= total_qty and total_qty > 0:
                rec.write({'state': 'fulfilled'})
            elif total_fulfilled > 0:
                rec.write({'state': 'partially_fulfilled'})
            elif rec.state in ('partially_fulfilled', 'fulfilled'):
                rec.write({'state': 'approved'})

    def export_material_request_excel(self):
        """Export Excel untuk Material Request"""
        return self.env['it_asset.excel_template'].export_material_request_excel(self.id)


class ITAssetMaterialRequestLine(models.Model):
    _name = 'it_asset.material_request.line'
    _description = 'Material Request Line'
    _order = 'id asc'

    request_id = fields.Many2one('it_asset.material_request', string='Request', required=True, ondelete='cascade')
    name = fields.Char(string='Item Name', required=True)
    description = fields.Char(string='Description')
    quantity = fields.Float(string='Requested Qty', default=1.0, required=True)
    qty_fulfilled = fields.Float(string='Fulfilled Qty', default=0.0, copy=False)
    qty_remaining = fields.Float(string='Remaining Qty', compute='_compute_qty_fulfillment', store=True)
    fulfillment_status = fields.Selection([
        ('pending', 'Pending'),
        ('partial', 'Partial'),
        ('fulfilled', 'Fulfilled'),
    ], string='Status', compute='_compute_qty_fulfillment', store=True)
    uom = fields.Char(string='Unit of Measure', default='Unit')    
    purpose = fields.Char(string='Untuk Kebutuhan')
    reason = fields.Char(string='Keterangan')
    notes = fields.Text(string='Notes')

    @api.depends('quantity', 'qty_fulfilled')
    def _compute_qty_fulfillment(self):
        for line in self:
            remaining = max(0.0, line.quantity - line.qty_fulfilled)
            line.qty_remaining = remaining
            if line.qty_fulfilled <= 0:
                line.fulfillment_status = 'pending'
            elif line.qty_fulfilled < line.quantity:
                line.fulfillment_status = 'partial'
            else:
                line.fulfillment_status = 'fulfilled'
