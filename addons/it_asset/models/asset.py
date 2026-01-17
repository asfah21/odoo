from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class ITAsset(models.Model):
    _name = 'it_asset.asset'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'IT Asset'
    _order = 'id desc'

    name = fields.Char(string='Asset Name', required=True, tracking=True)
    model = fields.Char(string='Model', tracking=True)
    specification = fields.Text(string='Specification')
    product_id = fields.Many2one('product.product', string='Product', required=True, ondelete='restrict', tracking=True)
    asset_tag = fields.Char(string='Asset Tag', tracking=True, copy=False)
    category_id = fields.Many2one('it_asset.category', string='Category', tracking=True)
    is_consumable = fields.Boolean(related='category_id.is_consumable', store=True)
    lot_id = fields.Many2one('stock.lot', string='Serial Number', tracking=True)
    employee_id = fields.Many2one('hr.employee', string='Assigned To', tracking=True)
    
    state = fields.Selection([
        ('available', 'Available'),
        ('in_use', 'In Use'),
        ('maintenance', 'Maintenance'),
        ('retired', 'Retired'),
    ], string='Status', default='available', tracking=True)
    
    condition = fields.Selection([
        ('good', 'Good'),
        ('degraded', 'Degraded'),
        ('broken', 'Broken'),
    ], string='Condition', default='good', tracking=True)

    usage_type = fields.Selection([
        ('personal', 'Personal'),
        ('shared', 'Shared'),
    ], string='Usage Type', default='personal', required=True, tracking=True)


    
    is_stock_synced = fields.Boolean(string='Stock Synced', default=False, readonly=True, tracking=False)
    assignment_ids = fields.One2many('it_asset.assignment', 'asset_id', string='Assignments')
    maintenance_ids = fields.One2many('it_asset.maintenance', 'asset_id', string='Maintenances')

    _sql_constraints = [
        ('unique_lot_id', 'unique(lot_id)', 'Serial number must be unique!'),
        ('unique_asset_tag', 'unique(asset_tag)', 'Asset Tag must be unique!')
    ]

    @api.depends('name', 'asset_tag')
    def _compute_display_name(self):
        for record in self:
            record.display_name = f"[{record.asset_tag}] {record.name}" if record.asset_tag else record.name

    # --- REFACTORED CORE LOGIC ---

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('product_id'):
                product = self.env['product.product'].browse(vals['product_id'])
                if product.type in ['product', 'storable']:

                    if vals.get('employee_id'):
                        vals['state'] = 'in_use'
                        vals['is_stock_synced'] = True
                    
                    # Pre-flight check (No manual locking)
                    if vals.get('state', 'available') not in ('retired', 'maintenance'):
                        self._preflight_stock_check(product, vals.get('lot_id'))

        records = super(ITAsset, self).create(vals_list)

        if not self.env.context.get('skip_stock_move'):
            for record in records:
                if record.employee_id:
                    record._trigger_stock_assignment(record.employee_id)
        
        return records

    def write(self, vals):
        # 1. Enforce Retired Read-Only Policy
        if any(r.state == 'retired' for r in self) and 'state' not in vals:
             raise UserError(_("Retired assets are read-only. Reactivate the asset to make changes."))

        # 2. Logic: Broken -> Maintenance
        if vals.get('condition') == 'broken':
            vals['state'] = 'maintenance'

        # 3. Usage Type Logic
        if 'employee_id' in vals:
            if vals['employee_id']:
                vals['state'] = 'in_use'
                vals['is_stock_synced'] = True
            else:
                 vals['state'] = 'available'

        if any(k in vals for k in ['product_id', 'lot_id', 'state']):
            for record in self:
                p_id = vals.get('product_id', record.product_id.id)
                l_id = vals.get('lot_id', record.lot_id.id)
                st = vals.get('state', record.state)
                product = self.env['product.product'].browse(p_id)
                
                if product.type in ['product', 'storable'] and st not in ['retired', 'maintenance']:
                    self._preflight_stock_check(product, l_id)

        old_employees = {r.id: r.employee_id.id for r in self}
        res = super(ITAsset, self).write(vals)

        if not self.env.context.get('skip_stock_move'):
            if 'employee_id' in vals:
                for record in self:
                    new_emp_id = vals['employee_id']
                    old_emp_id = old_employees.get(record.id)
                    
                    if new_emp_id and not old_emp_id:
                         record._trigger_stock_assignment(record.employee_id)
                    elif not new_emp_id and old_emp_id:
                         record._trigger_stock_return(self.env['hr.employee'].browse(old_emp_id))
                         # Optimization: Direct SQL to avoid recursive write checking
                         self.env.cr.execute("UPDATE it_asset_asset SET is_stock_synced = FALSE WHERE id = %s", (record.id,))
                         record.invalidate_recordset(['is_stock_synced'])
        return res

    # --- INTERNAL ENGINE ---

    def _preflight_stock_check(self, product, lot_id=None):
        """Friendly Pre-flight check before tech-op"""
        it_loc = self._get_it_location('it_source')
        domain = [('product_id', '=', product.id), ('location_id', 'child_of', it_loc.id)]
        if lot_id:
            domain.append(('lot_id', '=', lot_id.id if hasattr(lot_id, 'id') else lot_id))
            
        quants = self.env['stock.quant'].sudo().search(domain)
        if not quants:
            raise ValidationError(_("STOCK ERROR: Product/SN not found in IT Stock."))
        
        # Check Free Qty (Quantity - Reserved)
        free_qty = sum(quants.mapped(lambda q: q.quantity - q.reserved_quantity))
        if free_qty <= 0:
            raise ValidationError(_("STOCK UNAVAILABLE: Product exists but is already RESERVED for another operation."))

    def _get_it_location(self, type):
        param_key = f"it_asset.{type}_location_id"
        loc_id = self.env['ir.config_parameter'].sudo().get_param(param_key)
        
        if loc_id:
            loc = self.env['stock.location'].browse(int(loc_id))
            if loc.exists(): return loc

        wh = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        if not wh: wh = self.env['stock.warehouse'].search([], limit=1)
        if not wh: raise UserError(_("Setup Warehouse first."))
        
        if type == 'it_source':
            loc = self.env['stock.location'].search([('name', '=', 'IT'), ('location_id', '=', wh.lot_stock_id.id)], limit=1)
            if not loc:
                loc = self.env['stock.location'].create({
                    'name': 'IT', 'location_id': wh.lot_stock_id.id, 'usage': 'internal', 'company_id': self.env.company.id
                })
        else:
            parent = self._get_it_location('it_source')
            loc = self.env['stock.location'].search([('name', '=', 'User'), ('location_id', '=', parent.id)], limit=1)
            if not loc:
                loc = self.env['stock.location'].create({
                    'name': 'User', 'location_id': parent.id, 'usage': 'internal', 'company_id': self.env.company.id
                })
        
        self.env['ir.config_parameter'].sudo().set_param(param_key, loc.id)
        return loc

    def _create_it_stock_move(self, src, dest, reference):
        """Standard Odoo 18 Internal Transfer Logic with proper error handling"""
        ptype = self.env['stock.picking.type'].search([('code', '=', 'internal'), ('company_id', '=', self.env.company.id)], limit=1)
        if not ptype: raise UserError(_("Internal Picking Type missing."))
        
        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': ptype.id,
            'location_id': src.id,
            'location_dest_id': dest.id,
            'origin': self.name,
            'company_id': self.env.company.id,
        })
        
        move = self.env['stock.move'].sudo().create({
            'name': reference, 'product_id': self.product_id.id,
            'product_uom_qty': 1.0, 'product_uom': self.product_id.uom_id.id,
            'picking_id': picking.id, 'location_id': src.id, 'location_dest_id': dest.id,
        })
        
        picking.action_confirm()
        picking.action_assign()
        
        # HERE IS THE REAL ATOMICITY (Relying on Odoo internal engine)
        if picking.state == 'assigned':
            for line in picking.move_line_ids:
                if self.lot_id: line.lot_id = self.lot_id.id
                line.quantity = 1.0
                line.picked = True
            picking.button_validate()
        else:
            # Clean exit for failures
            picking.action_cancel()
            picking.unlink()
            raise UserError(_("STOCK RESERVATION FAILED: The item at %s could not be reserved. Perhaps it was just taken by another user.") % src.display_name)

    def _trigger_stock_assignment(self, employee):
        self._create_it_stock_move(self._get_it_location('it_source'), self._get_it_location('it_user'), _("User: %s") % employee.name)

    def _trigger_stock_return(self, employee):
        self._create_it_stock_move(self._get_it_location('it_user'), self._get_it_location('it_source'), _("Return: %s") % employee.name)

    # --- DASHBOARD (Optimized _read_group) ---

    @api.model
    def get_dashboard_stats(self, category_ids=None, date_start=None, date_end=None):
        domain = []
        if category_ids:
            domain.append(('category_id', 'in', category_ids))
        if date_start: domain.append(('create_date', '>=', date_start))
        if date_end: domain.append(('create_date', '<=', date_end))

        groups = self._read_group(domain, ['state', 'condition'], ['__count'])
        stats = {'total_assets': 0, 'available': 0, 'assigned': 0, 'unavailable_broken': 0}
        
        for state, condition, count in groups:
            stats['total_assets'] += count
            if state == 'available' and condition != 'broken': stats['available'] += count
            if state == 'in_use': stats['assigned'] += count
            if condition == 'broken': stats['unavailable_broken'] += count

        # 2. Category Distribution
        cat_groups = self._read_group(domain, ['category_id'], ['__count'])
        category_data = []
        total_assets = stats['total_assets'] or 1
        
        for cat, count in cat_groups:
            if cat: 
                category_data.append({
                    'name': cat.display_name,
                    'count': count,
                    'perc': (count / total_assets) * 100
                })

        m_domain = []
        if category_ids: m_domain.append(('asset_id.category_id', 'in', category_ids))
        if date_start: m_domain.append(('maintenance_date', '>=', date_start))
        
        stats.update({
            'maintenance_count': self.env['it_asset.maintenance'].search_count(m_domain),
            'laptop_condition_distribution': self._get_laptop_condition_stats(date_start, date_end, category_ids),
            'category_distribution': sorted(category_data, key=lambda x: x['count'], reverse=True)
        })
        return stats

    def _get_laptop_condition_stats(self, date_start, date_end, category_ids=None):
        laptop_cat = self.env['it_asset.category'].search([('name', 'ilike', 'laptop')], limit=1)
        if not laptop_cat: return []
        
        if category_ids and laptop_cat.id not in category_ids:
            return []
            
        domain = [('category_id', '=', laptop_cat.id)]
        if date_start: domain.append(('create_date', '>=', date_start))
        
        # Read groups and map to fixed structure
        groups = self._read_group(domain, ['condition'], ['__count'])
        data_map = {cond: count for cond, count in groups}
        
        res = []
        total = sum(data_map.values()) or 1
        definitions = [
            ('good', 'Good', '#22c55e'),
            ('degraded', 'Degraded', '#f59e0b'),
            ('broken', 'Broken', '#ef4444')
        ]
        
        for key, label, color in definitions:
            count = data_map.get(key, 0)
            res.append({
                'label': label,
                'count': count,
                'perc': (count/total)*100,
                'color': color
            })
        return res
