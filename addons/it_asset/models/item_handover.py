from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


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

    # ============================================================
    # Sequence
    # ============================================================
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

    # ============================================================
    # Actions
    # ============================================================
    def action_sign(self):
        """Sign and confirm the handover, process all lines"""
        self.ensure_one()

        if not self.line_ids:
            raise ValidationError(_("Please add at least one item to handover."))

        # Validate all lines before processing
        for line in self.line_ids:
            line._validate_before_sign()

        self.write({'state': 'signed'})

        # Process each line
        for line in self.line_ids:
            line._process_handover()

    def action_reset_to_draft(self):
        """Reset back to draft (for corrections)"""
        self.write({'state': 'draft'})


class ITItemHandoverLine(models.Model):
    _name = 'it_asset.item.handover.line'
    _description = 'Item Handover Line'
    _order = 'id asc'

    handover_id = fields.Many2one('it_asset.item.handover', string='Handover', required=True, ondelete='cascade')

    # Item Type: Asset or Consumable
    item_type = fields.Selection([
        ('asset', 'Asset'),
        ('consumable', 'Consumable'),
    ], string='Item Type', required=True, default='consumable')

    # If handing over an Asset
    asset_id = fields.Many2one('it_asset.asset', string='Asset',
                                domain="[('state', 'not in', ['retired'])]")

    # If handing over a Consumable
    consumable_id = fields.Many2one('it_asset.consumable', string='Consumable')
    quantity = fields.Float(string='Quantity', default=1.0, required=True,
                            help='Quantity of consumable items being handed over')

    # Dynamic display
    item_display = fields.Char(string='Item', compute='_compute_item_display', store=False)

    # Stock info (for display)
    qty_available = fields.Float(string='Available', related='consumable_id.qty_available', readonly=True)
    uom_name = fields.Char(string='UoM', related='consumable_id.uom_id.name', readonly=True)

    # ============================================================
    # Constraints & Validation
    # ============================================================
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
        """Validate stock availability before signing"""
        self.ensure_one()
        if self.item_type == 'consumable' and self.consumable_id:
            if self.quantity > self.consumable_id.qty_available:
                raise ValidationError(_(
                    "Not enough stock for '%s'. Available: %s %s, Requested: %s %s"
                ) % (
                    self.consumable_id.name,
                    self.consumable_id.qty_available,
                    self.uom_name or 'Unit',
                    self.quantity,
                    self.uom_name or 'Unit',
                ))

    # ============================================================
    # Computed Fields
    # ============================================================
    @api.depends('item_type', 'asset_id', 'consumable_id', 'quantity')
    def _compute_item_display(self):
        for record in self:
            if record.item_type == 'asset' and record.asset_id:
                record.item_display = record.asset_id.display_name
            elif record.item_type == 'consumable' and record.consumable_id:
                record.item_display = "%s (x%s)" % (record.consumable_id.name, record.quantity)
            else:
                record.item_display = False

    # ============================================================
    # Onchange
    # ============================================================
    @api.onchange('item_type')
    def _onchange_item_type(self):
        """Clear the other field when switching type"""
        if self.item_type == 'asset':
            self.consumable_id = False
            self.quantity = 1.0
        elif self.item_type == 'consumable':
            self.asset_id = False

    @api.onchange('consumable_id')
    def _onchange_consumable_id(self):
        """Auto-set quantity to 1 when consumable changes"""
        if self.consumable_id:
            self.quantity = 1.0

    # ============================================================
    # Processing Logic
    # ============================================================
    def _process_handover(self):
        """Process this line after handover is signed"""
        self.ensure_one()

        if self.item_type == 'consumable':
            self._reduce_consumable_stock()
        elif self.item_type == 'asset':
            self._update_asset_assignment()

    def _reduce_consumable_stock(self):
        """Reduce consumable stock via stock move (internal transfer)"""
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
        """Get or create IT stock location"""
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
                ('name', '=', 'IT'),
                ('location_id', '=', wh.lot_stock_id.id)
            ], limit=1)
            if not loc:
                loc = self.env['stock.location'].create({
                    'name': 'IT',
                    'location_id': wh.lot_stock_id.id,
                    'usage': 'internal',
                    'company_id': self.env.company.id,
                })
        else:
            parent = self._get_it_location('it_source')
            loc = self.env['stock.location'].search([
                ('name', '=', 'Consumed'),
                ('location_id', '=', parent.id)
            ], limit=1)
            if not loc:
                loc = self.env['stock.location'].create({
                    'name': 'Consumed',
                    'location_id': parent.id,
                    'usage': 'inventory',
                    'company_id': self.env.company.id,
                })

        self.env['ir.config_parameter'].sudo().set_param(param_key, loc.id)
        return loc

    def _get_consumed_location(self, parent_location):
        """Get or create 'Consumed' location under IT"""
        loc = self.env['stock.location'].search([
            ('name', '=', 'Consumed'),
            ('location_id', '=', parent_location.id)
        ], limit=1)
        if not loc:
            loc = self.env['stock.location'].create({
                'name': 'Consumed',
                'location_id': parent_location.id,
                'usage': 'inventory',
                'company_id': self.env.company.id,
            })
        return loc

    def _create_consumable_stock_move(self, product, src_location, dest_location):
        """Create stock move to reduce consumable quantity"""
        ptype = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('company_id', '=', self.env.company.id)
        ], limit=1)
        if not ptype:
            raise ValidationError(_("Internal Picking Type not found. Please configure warehouse."))

        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': ptype.id,
            'location_id': src_location.id,
            'location_dest_id': dest_location.id,
            'origin': '%s - %s' % (self.handover_id.name, self.item_display),
            'company_id': self.env.company.id,
        })

        move = self.env['stock.move'].sudo().create({
            'name': _('Item Handover: %s') % self.handover_id.name,
            'product_id': product.id,
            'product_uom_qty': self.quantity,
            'product_uom': product.uom_id.id,
            'picking_id': picking.id,
            'location_id': src_location.id,
            'location_dest_id': dest_location.id,
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
        """Update asset assignment when handing over an asset"""
        self.ensure_one()
        asset = self.asset_id
        if not asset:
            return

        try:
            self.env['it_asset.assignment'].create({
                'asset_id': asset.id,
                'employee_id': self.handover_id.receiver_id.id,
                'assignment_date': self.handover_id.handover_date,
                'state': 'active',
            })

            asset.write({
                'employee_id': self.handover_id.receiver_id.id,
                'state': 'in_use',
            })

            _logger.info("Asset %s assigned to %s via Item Handover", asset.name, self.handover_id.receiver_id.name)
        except Exception as e:
            _logger.error("Failed to update asset assignment: %s", str(e))
