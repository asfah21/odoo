from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class ITAsset(models.Model):
    _name = 'it_asset.asset'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'IT Asset'
    _order = 'id desc'

    asset_type = fields.Selection([
        ('it', 'IT Asset'),
        ('operation', 'Operation Asset'),
    ], string='Asset Type', default='it', required=True, tracking=True)

    name = fields.Char(string='Asset Name', required=True, tracking=True)
    model = fields.Char(string='Model', tracking=True)
    specification = fields.Text(string='Specification')
    product_id = fields.Many2one('product.product', string='Product', required=True, ondelete='restrict', tracking=True)
    asset_tag = fields.Char(string='Asset Tag', tracking=True, copy=False)
    category_id = fields.Many2one('it_asset.category', string='Category', tracking=True)
    is_consumable = fields.Boolean(related='category_id.is_consumable', store=True)
    lot_id = fields.Many2one('stock.lot', string='Serial Number', tracking=True)
    employee_id = fields.Many2one('hr.employee', string='Assigned To (User)', tracking=True)
    unit_id = fields.Many2one('it_asset.unit', string='Assigned Unit', tracking=True, help="Reference to Excavator, Dump Truck, etc.")
    
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
        ('personal', 'Personal (User)'),
        ('unit', 'Unit (Operation)'),
        ('shared', 'Shared'),
    ], string='Usage Type', default='personal', required=True, tracking=True)

    is_stock_synced = fields.Boolean(string='Stock Synced', default=False, readonly=True, tracking=False)
    assignment_ids = fields.One2many('it_asset.assignment', 'asset_id', string='Assignments')
    swap_ids = fields.One2many('it_asset.swap', 'asset_id', string='Swap History')
    maintenance_ids = fields.One2many('it_asset.maintenance', 'asset_id', string='Maintenances')
    printer_usage_ids = fields.One2many('it_asset.printer.usage', 'asset_id', string='Printer Usage Records')
    is_printer = fields.Boolean(compute='_compute_is_printer', store=True)
    damage_report_count = fields.Integer(compute='_compute_form_counts')
    handover_count = fields.Integer(compute='_compute_form_counts')

    def _compute_form_counts(self):
        for record in self:
            record.damage_report_count = self.env['it_asset.damage_report'].search_count([('asset_id', '=', record.id)])
            record.handover_count = self.env['it_asset.handover'].search_count([('asset_id', '=', record.id)])

    def action_view_damage_reports(self):
        return {
            'name': _('Damage Reports'),
            'type': 'ir.actions.act_window',
            'res_model': 'it_asset.damage_report',
            'view_mode': 'list,form',
            'domain': [('asset_id', '=', self.id)],
            'context': {'default_asset_id': self.id},
        }

    def action_view_handovers(self):
        return {
            'name': _('Handovers'),
            'type': 'ir.actions.act_window',
            'res_model': 'it_asset.handover',
            'view_mode': 'list,form',
            'domain': [('asset_id', '=', self.id)],
            'context': {'default_asset_id': self.id},
        }

    _sql_constraints = [
        ('unique_lot_id', 'unique(lot_id)', 'Serial number must be unique!'),
        ('unique_asset_tag', 'unique(asset_tag)', 'Asset Tag must be unique!')
    ]

    @api.depends('category_id')
    def _compute_is_printer(self):
        for record in self:
            record.is_printer = record.category_id and 'printer' in record.category_id.name.lower()

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

                    if vals.get('employee_id') or vals.get('unit_id'):
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
                elif record.unit_id:
                    record._trigger_stock_assignment(record.unit_id)
        
        return records

    def write(self, vals):
        # 1. Enforce Retired Read-Only Policy
        if any(r.state == 'retired' for r in self) and 'state' not in vals:
             raise UserError(_("Retired assets are read-only. Reactivate the asset to make changes."))

        # 2. Logic: Broken -> Maintenance
        if vals.get('condition') == 'broken':
            vals['state'] = 'maintenance'

        # 3. Usage Type Logic
        if 'employee_id' in vals or 'unit_id' in vals:
            if vals.get('employee_id') or vals.get('unit_id'):
                vals['state'] = 'in_use'
                vals['is_stock_synced'] = True
            else:
                 vals['state'] = 'available'
                 vals['is_stock_synced'] = False

        if any(k in vals for k in ['product_id', 'lot_id', 'state']):
            for record in self:
                p_id = vals.get('product_id', record.product_id.id)
                l_id = vals.get('lot_id', record.lot_id.id)
                st = vals.get('state', record.state)
                product = self.env['product.product'].browse(p_id)
                
                if product.type in ['product', 'storable'] and st not in ['retired', 'maintenance']:
                    self._preflight_stock_check(product, l_id)

        old_data = {r.id: {'emp': r.employee_id.id, 'unit': r.unit_id.id} for r in self}
        res = super(ITAsset, self).write(vals)

        if not self.env.context.get('skip_stock_move'):
            if 'employee_id' in vals or 'unit_id' in vals:
                for record in self:
                    new_emp_id = vals.get('employee_id', record.employee_id.id)
                    new_unit_id = vals.get('unit_id', record.unit_id.id)
                    old_emp_id = old_data[record.id]['emp']
                    old_unit_id = old_data[record.id]['unit']
                    
                    # Trigger assignment if newly assigned
                    if (new_emp_id and not old_emp_id) or (new_unit_id and not old_unit_id):
                        target = record.employee_id if new_emp_id else record.unit_id
                        record._trigger_stock_assignment(target)
                    # Trigger return if unassigned
                    elif (not new_emp_id and old_emp_id) or (not new_unit_id and old_unit_id):
                        old_target = self.env['hr.employee'].browse(old_emp_id) if old_emp_id else self.env['it_asset.unit'].browse(old_unit_id)
                        record._trigger_stock_return(old_target)
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

    def _trigger_stock_assignment(self, target):
        self._create_it_stock_move(self._get_it_location('it_source'), self._get_it_location('it_user'), _("Assigned: %s") % target.name)

    def _trigger_stock_return(self, target):
        self._create_it_stock_move(self._get_it_location('it_user'), self._get_it_location('it_source'), _("Return: %s") % target.name)

    # --- DASHBOARD (Optimized _read_group) ---

    @api.model
    def get_dashboard_stats(self, date_start=None, date_end=None, category_ids=None, fleet_category_ids=None, comp_asset_cat_ids=None, printer_period='7D'):
        """Fetch all dashboard statistics in one call"""
        # Convert JS null/string 'null' to Python None
        if not printer_period: printer_period = '7D'
        if date_start == 'null' or not date_start: date_start = None
        if date_end == 'null' or not date_end: date_end = None
        
        domain = []
        if category_ids:
            domain.append(('category_id', 'in', category_ids))
        if date_start: domain.append(('create_date', '>=', date_start))
        if date_end: domain.append(('create_date', '<=', date_end))

        # Basic Stats
        stats = {
            'total_assets': self.search_count(domain),
            'total_it': self.search_count(domain + [('asset_type', '=', 'it')]),
            'total_operation': self.search_count(domain + [('asset_type', '=', 'operation')]),
            'available': self.search_count(domain + [('asset_type', '=', 'it'), ('state', '=', 'available')]),
            'assigned': self.search_count(domain + [('asset_type', '=', 'it'), ('state', '=', 'in_use')]),
            'unavailable_broken': self.search_count(domain + [('asset_type', '=', 'it'), '|', ('condition', '=', 'broken'), ('state', '=', 'retired')]),
            'op_available': 0, 'op_assigned': 0, 'op_unavailable_broken': 0, 'op_maintenance': 0,
            'tickets_open': 0, 'account_requests_pending': 0 # Placeholders
        }

        # 1. Operational Stats Grouping
        op_groups = self._read_group(domain + [('asset_type', '=', 'operation')], ['state', 'condition'], ['__count'])
        for state, condition, count in op_groups:
            if condition == 'broken' or state == 'retired':
                stats['op_unavailable_broken'] += count
            elif state == 'available':
                stats['op_available'] += count
            elif state == 'in_use':
                stats['op_assigned'] += count
            elif state == 'maintenance':
                stats['op_maintenance'] += count

        # 2. Category Distribution
        cat_groups = self._read_group(domain + [('asset_type', '=', 'it')], ['category_id'], ['__count'])
        category_data = []
        
        for cat, count in cat_groups:
            if cat: 
                category_data.append({
                    'name': cat.display_name,
                    'count': count,
                    'perc': (count / (stats['total_it'] or 1)) * 100
                })

        m_domain = [('asset_id.asset_type', '=', 'it')]
        if category_ids: m_domain.append(('asset_id.category_id', 'in', category_ids))
        if date_start: m_domain.append(('maintenance_date', '>=', date_start))
        
        stats.update({
            'maintenance_count': self.env['it_asset.maintenance'].search_count(m_domain),
            'laptop_condition_distribution': self._get_laptop_condition_stats(date_start, date_end, category_ids),
            'category_distribution': sorted(category_data, key=lambda x: x['count'], reverse=True),
            'fleet_comparison': self._get_fleet_comparison_stats(comp_asset_cat_ids, fleet_category_ids),
            'printer_stats': self._get_printer_dashboard_stats(printer_period)
        })
        return stats

    def _get_laptop_condition_stats(self, date_start, date_end, category_ids=None):
        laptop_cat = self.env['it_asset.category'].search([('name', 'ilike', 'laptop')], limit=1)
        if not laptop_cat: return {'total': 0, 'data': []}
        
        if category_ids and laptop_cat.id not in category_ids:
            return {'total': 0, 'data': []}
            
        domain = [('category_id', '=', laptop_cat.id)]
        if date_start: domain.append(('create_date', '>=', date_start))
        
        # Read groups and map to fixed structure
        groups = self._read_group(domain, ['condition'], ['__count'])
        data_map = {cond: count for cond, count in groups}
        
        res = []
        actual_total = sum(data_map.values())
        total_for_perc = actual_total or 1
        
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
                'perc': (count/total_for_perc)*100,
                'color': color
            })
        return {
            'total': actual_total,
            'data': res
        }

    def _get_fleet_comparison_stats(self, asset_cat_ids=None, fleet_cat_ids=None):
        """Compare Operational Assets vs Fleet Units with specific filtering"""
        # 1. Get Operational Assets
        asset_domain = [('asset_type', '=', 'operation')]
        if asset_cat_ids:
            asset_domain.append(('category_id', 'in', asset_cat_ids))
        asset_count = self.search_count(asset_domain)

        # 2. Get Fleet Units
        unit_domain = []
        if fleet_cat_ids:
            unit_domain.append(('category_id', 'in', fleet_cat_ids))
        unit_count = self.env['it_asset.unit'].search_count(unit_domain)

        return {
            'assets': asset_count,
            'units': unit_count,
            'ratio': (asset_count / (unit_count or 1)) * 100, # Percentage fill
            'asset_cat_ids': asset_cat_ids or [],
            'fleet_cat_ids': fleet_cat_ids or []
        }

    def _get_printer_dashboard_stats(self, period='7D'):
        """Fetch printer usage summary for the dashboard"""
        Usage = self.env['it_asset.printer.usage']
        
        # Determine Date Range
        if period == 'ALL':
            # For ALL, we show the LATEST absolute readings for each printer
            latest_usage_ids = Usage._read_group([], ['asset_id'], ['id:max'])
            ids = [row[1] for row in latest_usage_ids if row[1]]
            latest_records = Usage.browse(ids)
            
            total_bw = sum(latest_records.mapped('bw_pages'))
            total_color = sum(latest_records.mapped('color_pages'))
            total_printed = total_bw + total_color
        else:
            # For specific periods, we calculate GROWTH (Last - Base)
            days = 7
            if period == '1M': days = 30
            elif period == '1Y': days = 365
            
            end_date = fields.Date.today()
            start_date = fields.Date.subtract(end_date, days=days)
            
            # 1. Get all printers that have usage
            printer_ids = Usage._read_group([], ['asset_id'])
            
            total_bw = 0
            total_color = 0
            total_printed = 0
            
            for [printer] in printer_ids:
                if not printer: continue
                
                # Latest record in period
                last_in = Usage.search([
                    ('asset_id', '=', printer.id),
                    ('date', '<=', end_date),
                    ('date', '>=', start_date)
                ], order='date desc, id desc', limit=1)
                
                if not last_in: continue
                
                # Base record (the one just before or at start of period)
                base = Usage.search([
                    ('asset_id', '=', printer.id),
                    ('date', '<', start_date)
                ], order='date desc, id desc', limit=1)
                
                if not base:
                    # If no record before period, usage is growth from first record in period
                    first_in = Usage.search([
                        ('asset_id', '=', printer.id),
                        ('date', '>=', start_date)
                    ], order='date asc, id asc', limit=1)
                    base = first_in
                
                if last_in and base:
                    total_bw += (last_in.bw_pages - base.bw_pages)
                    total_color += (last_in.color_pages - base.color_pages)
                    total_printed += (last_in.total_pages - base.total_pages)

        return {
            'total_color': total_color,
            'total_bw': total_bw,
            'total_pages': total_printed,
            'period': period
        }
