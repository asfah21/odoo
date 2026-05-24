from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)

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


class ITItemHandover(models.Model):
    _name = 'it_asset.item.handover'
    _description = 'Item Handover'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'handover_date desc, id desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))

    # People
    sender_id = fields.Many2one('hr.employee', string='Sent By', required=True,
                                 default=lambda self: self.env.user.employee_id)
    receiver_id = fields.Many2one('hr.employee', string='Received By', required=True)
    handover_date = fields.Date(string='Handover Date', default=fields.Date.context_today, required=True)

    # Notes & Signature
    notes = fields.Text(string='Notes')
    signature = fields.Binary(string='Receiver Signature', help='Signature of the person receiving the item')

    # State
    state = fields.Selection([
        ('draft', 'Draft'),
        ('signed', 'Signed'),
    ], string='Status', default='draft', tracking=True)

    # One2many Lines
    line_ids = fields.One2many('it_asset.item.handover.line', 'handover_id', string='Items')

    # Computed summary
    total_items = fields.Integer(string='Total Items', compute='_compute_summary', store=True)
    total_consumable_qty = fields.Float(string='Total Consumable Qty', compute='_compute_summary', store=True)

    @api.depends('line_ids')
    def _compute_summary(self):
        for record in self:
            record.total_items = len(record.line_ids)
            record.total_consumable_qty = sum(
                line.quantity for line in record.line_ids if line.item_type == 'consumable'
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                seq = self.env['ir.sequence'].next_by_code('it_asset.item.handover') or _('New')
                if seq != _('New'):
                    handover_date = vals.get('handover_date')
                    if handover_date:
                        handover_date = fields.Date.from_string(handover_date)
                    else:
                        handover_date = fields.Date.today()
                    year = handover_date.year
                    month = handover_date.month
                    roman_map = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V', 6: 'VI',
                                 7: 'VII', 8: 'VIII', 9: 'IX', 10: 'X', 11: 'XI', 12: 'XII'}
                    roman_month = roman_map.get(month, 'I')
                    if seq.count('/') < 2:
                        vals['name'] = f"{seq}/{roman_month}/FSTB/GSI-IT/{year}"
                    else:
                        vals['name'] = seq
                else:
                    vals['name'] = seq
        return super().create(vals_list)

    def action_sign(self):
        self.ensure_one()
        if not self.line_ids:
            raise ValidationError(_("Please add at least one item to handover."))
        for line in self.line_ids:
            line._validate_before_sign()
        self.write({'state': 'signed'})
        for line in self.line_ids:
            line._process_handover()

    def action_reset_to_draft(self):
        self.write({'state': 'draft'})


class ITItemHandoverLine(models.Model):
    _name = 'it_asset.item.handover.line'
    _description = 'Item Handover Line'
    _order = 'id asc'

    handover_id = fields.Many2one('it_asset.item.handover', string='Handover', required=True, ondelete='cascade')

    item_type = fields.Selection([
        ('asset', 'Asset'),
        ('consumable', 'Consumable'),
    ], string='Item Type', required=True, default='consumable')

    asset_id = fields.Many2one('it_asset.asset', string='Asset',
                                domain="[('state', 'not in', ['retired'])]")
    consumable_id = fields.Many2one('it_asset.consumable', string='Consumable')
    quantity = fields.Float(string='Quantity', default=1.0, required=True)

    notes = fields.Text(string='Notes')
    item_display = fields.Char(string='Item', compute='_compute_item_display', store=False)
    qty_available = fields.Float(string='Available', related='consumable_id.qty_available', readonly=True)
    uom_name = fields.Char(string='UoM', related='consumable_id.uom_id.name', readonly=True)

    @api.constrains('item_type', 'asset_id', 'consumable_id', 'quantity')
    def _check_item_selection(self):
        for record in self:
            if record.item_type == 'asset' and not record.asset_id:
                raise ValidationError(_("Please select an Asset for Asset handover."))
            if record.item_type == 'consumable' and not record.consumable_id:
                raise ValidationError(_("Please select a Consumable for Consumable handover."))
            if record.item_type == 'consumable' and record.quantity <= 0:
                raise ValidationError(_("Quantity must be greater than 0."))

    def _validate_before_sign(self):
        self.ensure_one()
        if self.item_type == 'consumable' and self.consumable_id:
            if self.quantity > self.consumable_id.qty_available:
                raise ValidationError(_(
                    "Not enough stock for '%s'. Available: %s %s, Requested: %s %s"
                ) % (self.consumable_id.name, self.consumable_id.qty_available,
                     self.uom_name or 'Unit', self.quantity, self.uom_name or 'Unit'))

    @api.depends('item_type', 'asset_id', 'consumable_id', 'quantity')
    def _compute_item_display(self):
        for record in self:
            if record.item_type == 'asset' and record.asset_id:
                record.item_display = record.asset_id.display_name
            elif record.item_type == 'consumable' and record.consumable_id:
                record.item_display = "%s (x%s)" % (record.consumable_id.name, record.quantity)
            else:
                record.item_display = False

    @api.onchange('item_type')
    def _onchange_item_type(self):
        if self.item_type == 'asset':
            self.consumable_id = False
            self.quantity = 1.0
        elif self.item_type == 'consumable':
            self.asset_id = False

    @api.onchange('consumable_id')
    def _onchange_consumable_id(self):
        if self.consumable_id:
            self.quantity = 1.0

    def _process_handover(self):
        self.ensure_one()
        if self.item_type == 'consumable':
            self._reduce_consumable_stock()
        elif self.item_type == 'asset':
            self._update_asset_assignment()

    def _reduce_consumable_stock(self):
        self.ensure_one()
        consumable = self.consumable_id
        product = consumable.product_id
        if not product:
            _logger.warning("Consumable %s has no linked product, skipping stock reduction", consumable.name)
            return
        try:
            it_loc = self._get_it_location('it_source')
            consumed_loc = self._get_consumed_location(it_loc)
            self._create_consumable_stock_move(product, it_loc, consumed_loc)
            _logger.info("Stock reduced for consumable %s: -%s %s",
                         consumable.name, self.quantity, product.uom_id.name)
        except Exception as e:
            _logger.error("Failed to reduce stock for consumable %s: %s", consumable.name, str(e))
            raise ValidationError(_("Failed to update stock: %s") % str(e))

    def _get_it_location(self, location_type):
        param_key = f"it_asset.{location_type}_location_id"
        loc_id = self.env['ir.config_parameter'].sudo().get_param(param_key)
        if loc_id:
            loc = self.env['stock.location'].browse(int(loc_id))
            if loc.exists():
                return loc
        wh = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        if not wh:
            wh = self.env['stock.warehouse'].search([], limit=1)
        if not wh:
            raise ValidationError(_("Please setup a Warehouse first."))
        if location_type == 'it_source':
            loc = self.env['stock.location'].search([
                ('name', '=', 'IT'), ('location_id', '=', wh.lot_stock_id.id)
            ], limit=1)
            if not loc:
                loc = self.env['stock.location'].create({
                    'name': 'IT', 'location_id': wh.lot_stock_id.id,
                    'usage': 'internal', 'company_id': self.env.company.id,
                })
        else:
            parent = self._get_it_location('it_source')
            loc = self.env['stock.location'].search([
                ('name', '=', 'Consumed'), ('location_id', '=', parent.id)
            ], limit=1)
            if not loc:
                loc = self.env['stock.location'].create({
                    'name': 'Consumed', 'location_id': parent.id,
                    'usage': 'inventory', 'company_id': self.env.company.id,
                })
        self.env['ir.config_parameter'].sudo().set_param(param_key, loc.id)
        return loc

    def _get_consumed_location(self, parent_location):
        loc = self.env['stock.location'].search([
            ('name', '=', 'Consumed'), ('location_id', '=', parent_location.id)
        ], limit=1)
        if not loc:
            loc = self.env['stock.location'].create({
                'name': 'Consumed', 'location_id': parent_location.id,
                'usage': 'inventory', 'company_id': self.env.company.id,
            })
        return loc

    def _create_consumable_stock_move(self, product, src_location, dest_location):
        ptype = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'), ('company_id', '=', self.env.company.id)
        ], limit=1)
        if not ptype:
            raise ValidationError(_("Internal Picking Type not found. Please configure warehouse."))
        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': ptype.id, 'location_id': src_location.id,
            'location_dest_id': dest_location.id,
            'origin': '%s - %s' % (self.handover_id.name, self.item_display),
            'company_id': self.env.company.id,
        })
        move = self.env['stock.move'].sudo().create({
            'name': _('Item Handover: %s') % self.handover_id.name,
            'product_id': product.id, 'product_uom_qty': self.quantity,
            'product_uom': product.uom_id.id, 'picking_id': picking.id,
            'location_id': src_location.id, 'location_dest_id': dest_location.id,
        })
        picking.action_confirm()
        picking.action_assign()
        if picking.state == 'assigned':
            for line in picking.move_line_ids:
                line.quantity = self.quantity
                line.picked = True
            picking.button_validate()
        else:
            picking.action_cancel()
            picking.unlink()
            raise ValidationError(_(
                "Stock reservation failed. Not enough stock available for '%s'."
            ) % product.display_name)

    def _update_asset_assignment(self):
        self.ensure_one()
        asset = self.asset_id
        if not asset:
            return
        try:
            self.env['it_asset.assignment'].create({
                'asset_id': asset.id, 'employee_id': self.handover_id.receiver_id.id,
                'assignment_date': self.handover_id.handover_date, 'state': 'active',
            })
            asset.write({'employee_id': self.handover_id.receiver_id.id, 'state': 'in_use'})
            _logger.info("Asset %s assigned to %s via Item Handover", asset.name, self.handover_id.receiver_id.name)
        except Exception as e:
            _logger.error("Failed to update asset assignment: %s", str(e))


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
                seq = self.env['ir.sequence'].next_by_code('it_asset.handover') or _('New')
                if seq != _('New'):
                    handover_date = vals.get('handover_date')
                    if handover_date:
                        handover_date = fields.Date.from_string(handover_date)
                    else:
                        handover_date = fields.Date.today()
                    
                    year = handover_date.year
                    month = handover_date.month
                    roman_map = {1: 'I', 2: 'II', 3: 'III', 4: 'IV', 5: 'V', 6: 'VI', 
                                 7: 'VII', 8: 'VIII', 9: 'IX', 10: 'X', 11: 'XI', 12: 'XII'}
                    roman_month = roman_map.get(month, 'I')

                    # Prevent double formatting if XML didn't update
                    if seq.count('/') < 2:
                        vals['name'] = f"{seq}/{roman_month}/BAST/GSI-IT/{year}"
                    else:
                        vals['name'] = seq
                else:
                    vals['name'] = seq
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

    # Signature / Approval fields
    user_id = fields.Many2one('hr.employee', string='User')
    verified_by_id = fields.Many2one('hr.employee', string='Diverifikasi Oleh')
    known_by_id = fields.Many2one('hr.employee', string='Diketahui Oleh')
    approved_by_id = fields.Many2one('hr.employee', string='Disetujui Oleh')

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
                else:
                    vals['name'] = seq
        return super().create(vals_list)

    def action_confirm(self):
        self.write({'state': 'confirmed'})
        if self.asset_id:
            self.asset_id.with_context(from_damage_report=True).write({'condition': 'broken'})

    def action_resolve(self):
        self.write({'state': 'resolved'})


class ITAccountRequest(models.Model):
    _name = 'it_asset.account_request'
    _description = 'Account Request Form'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    employee_id = fields.Many2one('hr.employee', string='Requester', required=True, default=lambda self: self.env.user.employee_id)
    department_id = fields.Many2one('hr.department', string='Department', related='employee_id.department_id', readonly=True)
    account_type = fields.Selection([
        ('email', 'Email'),
        ('google_workspace', 'Google Workspace'),
        ('accurate', 'Accurate')
    ], string='Account Type', required=True)
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
                vals['name'] = self.env['ir.sequence'].next_by_code('it_asset.account_request') or _('New')
        return super().create(vals_list)

    def action_submit(self):
        self.write({'state': 'submitted'})

    def action_approve(self):
        self.write({'state': 'approved'})

    def action_reject(self):
        self.write({'state': 'rejected'})

    def action_fulfill(self):
        self.write({'state': 'fulfilled'})
