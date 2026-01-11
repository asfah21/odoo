from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging
_logger = logging.getLogger(__name__)

class ITAsset(models.Model):
    _name = 'it_asset.asset'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'IT Asset'

    name = fields.Char(string='Asset Name', required=True, tracking=True)
    model = fields.Char(string='Model', tracking=True)
    specification = fields.Text(string='Specification')
    product_id = fields.Many2one('product.product', string='Product', required=True, ondelete='restrict', tracking=True)
    asset_tag = fields.Char(string='Asset Tag', tracking=True)
    category_id = fields.Many2one('it_asset.category', string='Category', tracking=True)
    is_consumable = fields.Boolean(related='category_id.is_consumable', store=True)
    lot_id = fields.Many2one('stock.lot', string='Serial Number', tracking=True)
    employee_id = fields.Many2one('hr.employee', string='Assigned To', tracking=True)
    state = fields.Selection([
        ('available', 'Available'),
        ('assigned', 'Assigned'),
        ('repair', 'Under Repair'),
        ('retired', 'Retired'),
    ], string='Status', default='available', tracking=True)
    condition = fields.Selection([
        ('good', 'Good'),
        ('damaged', 'Damaged'),
        ('broken', 'Broken'),
    ], string='Condition', default='good', tracking=True)
    
    is_stock_synced = fields.Boolean(string='Stock Synced', default=False, readonly=True)
    assignment_ids = fields.One2many('it_asset.assignment', 'asset_id', string='Assignments')
    maintenance_ids = fields.One2many('it_asset.maintenance', 'asset_id', string='Maintenances')

    _sql_constraints = [
        ('unique_lot_id', 'unique(lot_id)', 'Serial number must be unique!'),
        ('unique_asset_tag', 'unique(asset_tag)', 'Asset Tag must be unique!')
    ]

    # --- CORE LOGIC ---

    @api.model_create_multi
    def create(self, vals_list):
        """Strict: Block asset creation if stock is empty (Odoo 18)"""
        for vals in vals_list:
            if 'product_id' in vals:
                product = self.env['product.product'].browse(vals['product_id'])
                # Check product type (Odoo 18 uses 'storable')
                if product.type in ['product', 'storable']:
                    self._enforce_it_stock_rule(product, vals.get('lot_id'))
        
        records = super().create(vals_list)
        for record in records:
            if record.employee_id and record.state == 'available':
                record.state = 'assigned'
                record._trigger_stock_assignment(record.employee_id)
                record.is_stock_synced = True
        return records

    def write(self, vals):
        """Block update if it violates stock rules"""
        for record in self:
            if any(k in vals for k in ['product_id', 'lot_id', 'state']):
                product = self.env['product.product'].browse(vals.get('product_id', record.product_id.id))
                lot_id = vals.get('lot_id', record.lot_id.id)
                state = vals.get('state', record.state)
                
                if product.type in ['product', 'storable'] and state not in ['retired', 'repair']:
                    self._enforce_it_stock_rule(product, lot_id)

            if 'employee_id' in vals:
                new_employee = self.env['hr.employee'].browse(vals['employee_id']) if vals['employee_id'] else False
                if new_employee and not record.employee_id:
                    record.state = 'assigned'
                    record._trigger_stock_assignment(new_employee)
                    record.is_stock_synced = True
                elif not new_employee and record.employee_id:
                    record.state = 'available'
                    record._trigger_stock_return(record.employee_id)
                    record.is_stock_synced = False
        return super().write(vals)

    # --- VALIDATION HELPERS ---

    def _enforce_it_stock_rule(self, product, lot_id=None):
        """Validate Physical Stock in WH/Stock/IT"""
        _logger.info("ENFORCING STOCK RULE for Product: %s (Type: %s)", product.name, product.type)
        it_loc = self._get_it_source_location()
        
        # Search stock specifically in WH/Stock/IT and its children (like IT/User)
        domain = [
            ('product_id', '=', product.id),
            ('location_id', 'child_of', it_loc.id),
            ('quantity', '>', 0)
        ]
        if lot_id:
            domain.append(('lot_id', '=', lot_id))
            
        quants = self.env['stock.quant'].sudo().search(domain)
        available_qty = sum(quants.mapped('quantity'))
        
        if available_qty <= 0:
            msg = _("BLOCK: Product '%s' is not available in WH/Stock/IT.") % product.name
            if lot_id:
                lot_name = self.env['stock.lot'].browse(lot_id).name
                msg = _("BLOCK: SN '%s' is not available in WH/Stock/IT.") % lot_name
            
            _logger.error("STOCK VALIDATION FAILED: %s", msg)
            raise UserError(msg + _("\n\nSOLUTION: Please perform an internal transfer to WH/Stock/IT first in Inventory."))

    # --- LOCATION HELPERS ---

    def _get_it_source_location(self):
        """Get or Create WH/Stock/IT"""
        loc = self.env['stock.location'].search([('complete_name', 'ilike', 'WH/Stock/IT')], limit=1)
        if not loc:
            wh = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1) or self.env['stock.warehouse'].search([], limit=1)
            if not wh: raise UserError(_("Please setup Inventory first! (Warehouse not found)"))
            loc = self.env['stock.location'].create({
                'name': 'IT', 'location_id': wh.lot_stock_id.id, 'usage': 'internal', 'company_id': self.env.company.id
            })
        return loc

    def _get_it_dest_location(self):
        """Get or Create WH/Stock/IT/User"""
        loc = self.env['stock.location'].search([('complete_name', 'ilike', 'WH/Stock/IT/User')], limit=1)
        if not loc:
            parent = self._get_it_source_location()
            loc = self.env['stock.location'].create({
                'name': 'User', 'location_id': parent.id, 'usage': 'internal', 'company_id': self.env.company.id
            })
        return loc

    def _get_internal_picking_type(self):
        """Finds internal transfer type"""
        ptype = self.env['stock.picking.type'].search([('code', '=', 'internal'), ('company_id', '=', self.env.company.id)], limit=1)
        return ptype or self.env['stock.picking.type'].search([('code', '=', 'internal')], limit=1)

    # --- ACTION METHODS ---

    def action_sync_inventory(self):
        self.ensure_one()
        if self.is_stock_synced: raise UserError(_("Already synced."))
        if self.employee_id:
            self._trigger_stock_assignment(self.employee_id)
            self.is_stock_synced = True
            self.message_post(body=_("✅ Stock Synced Automatically"))
        return True

    def _trigger_stock_assignment(self, employee):
        source = self._get_it_source_location()
        dest = self._get_it_dest_location()
        self._create_it_stock_move(source, dest, _("Issue to %s") % employee.name)

    def _trigger_stock_return(self, employee):
        source = self._get_it_dest_location()
        dest = self._get_it_source_location()
        self._create_it_stock_move(source, dest, _("Return from %s") % employee.name)

    def _create_it_stock_move(self, src, dest, reference):
        """Final Odoo 18 Internal Transfer Logic"""
        ptype = self._get_internal_picking_type()
        if not ptype: raise UserError(_("Picking Type 'Internal' not found."))
        
        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': ptype.id,
            'location_id': src.id,
            'location_dest_id': dest.id,
            'origin': self.name,
            'company_id': self.env.company.id,
        })
        
        move = self.env['stock.move'].sudo().create({
            'name': reference,
            'product_id': self.product_id.id,
            'product_uom_qty': 1.0,
            'product_uom': self.product_id.uom_id.id,
            'picking_id': picking.id,
            'location_id': src.id,
            'location_dest_id': dest.id,
        })
        
        picking.action_confirm()
        picking.action_assign()
        
        if picking.state != 'assigned':
            picking.unlink()
            raise UserError(_("Insufficient stock at %s (it might be reserved by another transaction).") % src.display_name)
            
        for line in picking.move_line_ids:
            if self.lot_id: line.lot_id = self.lot_id.id
            line.quantity = 1.0
            line.picked = True # Vital for Odoo 18
            
        picking.button_validate()
        return True

    @api.depends('name', 'asset_tag')
    def _compute_display_name(self):
        for record in self:
            if record.asset_tag:
                record.display_name = f"[{record.asset_tag}] {record.name}"
            else:
                record.display_name = record.name

    @api.model
    def get_dashboard_stats(self, category_id=None, date_start=None, date_end=None):
        """
        Returns stats for the dashboard with actual data for Odoo 18
        """
        domain = []
        m_domain = [] # Maintenance domain
        if category_id:
            domain.append(('category_id', '=', int(category_id)))
            m_domain.append(('asset_id.category_id', '=', int(category_id)))
        
        if date_start:
            domain.append(('create_date', '>=', date_start))
            m_domain.append(('maintenance_date', '>=', date_start))
        if date_end:
            domain.append(('create_date', '<=', date_end))
            m_domain.append(('maintenance_date', '<=', date_end))

        # Asset Stats
        total_assets = self.search_count(domain)
        available = self.search_count(domain + [('state', '=', 'available'), ('condition', '!=', 'broken')])
        assigned = self.search_count(domain + [('state', '=', 'assigned')])
        unavailable_broken = self.search_count(domain + [('condition', '=', 'broken')])
        repair_state = self.search_count(domain + [('state', '=', 'repair')])
        retired = self.search_count(domain + [('state', '=', 'retired')])

        # Maintenance Stats (Real logs count)
        maintenance_count = self.env['it_asset.maintenance'].search_count(m_domain)

        # Distribution by Category
        categories = self.env['it_asset.category'].search([])
        category_data = []
        for cat in categories:
            cat_domain = [('category_id', '=', cat.id)]
            if date_start: cat_domain.append(('create_date', '>=', date_start))
            if date_end: cat_domain.append(('create_date', '<=', date_end))
            
            count = self.search_count(cat_domain)
            if count > 0 or not category_id: 
                category_data.append({
                    'name': cat.name,
                    'count': count
                })

        # Distribution by State
        state_data = [
            {'label': 'Available', 'value': available, 'color': '#22c55e'},
            {'label': 'Assigned', 'value': assigned, 'color': '#3b82f6'},
            {'label': 'Broken', 'value': unavailable_broken, 'color': '#ef4444'}]

        return {
            'total_assets': total_assets,
            'available': available,
            'assigned': assigned,
            'unavailable_broken': unavailable_broken,
            'maintenance_count': maintenance_count,
            'repair_state': repair_state,
            'retired': retired,
            'category_distribution': sorted(category_data, key=lambda x: x['count'], reverse=True)[:10],
            'state_distribution': state_data,
            'laptop_condition_distribution': self._get_laptop_condition_stats(date_start, date_end),
        }

    def _get_laptop_condition_stats(self, date_start, date_end):
        """Helper to get condition breakdown for Laptops specifically"""
        # Find category named 'Laptop' (case insensitive)
        laptop_cat = self.env['it_asset.category'].search([('name', 'ilike', 'laptop')], limit=1)
        if not laptop_cat:
            return []
            
        domain = [('category_id', '=', laptop_cat.id)]
        if date_start: domain.append(('create_date', '>=', date_start))
        if date_end: domain.append(('create_date', '<=', date_end))
        
        good = self.search_count(domain + [('condition', '=', 'good')])
        damaged = self.search_count(domain + [('condition', '=', 'damaged')])
        broken = self.search_count(domain + [('condition', '=', 'broken')])
        
        total = good + damaged + broken or 1
        
        return [
            {'label': 'Good', 'count': good, 'perc': (good/total)*100, 'color': '#22c55e'},
            {'label': 'Damaged', 'count': damaged, 'perc': (damaged/total)*100, 'color': '#f59e0b'},
            {'label': 'Broken', 'count': broken, 'perc': (broken/total)*100, 'color': '#ef4444'},
        ]
