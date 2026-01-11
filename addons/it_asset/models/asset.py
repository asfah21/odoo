from odoo import models, fields, api

class ITAsset(models.Model):
    _name = 'it_asset.asset'
    _description = 'IT Asset'

    name = fields.Char(string='Asset Name', required=True)
    model = fields.Char(string='Model')
    specification = fields.Text(string='Spesifikasi')
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True,
        ondelete='restrict'
    )
    asset_tag = fields.Char(string='Asset Tag')
    category_id = fields.Many2one(
        'it_asset.category',
        string='Category'
    )
    is_consumable = fields.Boolean(related='category_id.is_consumable', store=True)
    lot_id = fields.Many2one(
        'stock.lot',
        string='Serial Number'
    )
    employee_id = fields.Many2one(
        'hr.employee',
        string='Assigned To'
    )
    state = fields.Selection(
        [
            ('available', 'Available'),
            ('assigned', 'Assigned'),
            ('repair', 'Under Repair'),
            ('retired', 'Retired'),
        ],
        string='Status',
        default='available'
    )
    assignment_ids = fields.One2many(
        'it_asset.assignment',
        'asset_id',
        string='Assignments'
    )
    maintenance_ids = fields.One2many(
        'it_asset.maintenance',
        'asset_id',
        string='Maintenances'
    )

    _sql_constraints = [
        (
            'unique_lot_id',
            'unique(lot_id)',
            'This serial number is already assigned to another asset.'
        ),
        (
            'unique_asset_tag',
            'unique(asset_tag)',
            'Asset Tag must be unique!'
        )
    ]

    @api.constrains('asset_tag', 'is_consumable')
    def _check_asset_tag(self):
        for record in self:
            if not record.is_consumable and not record.asset_tag:
                raise models.ValidationError("Asset Tag is required for non-consumable assets.")

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            return {
                'domain': {
                    'lot_id': [('product_id', '=', self.product_id.id)]
                }
            }
        return {'domain': {'lot_id': []}}

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        for record in self:
            if record.state in ('repair', 'retired'):
                continue
            if record.employee_id:
                record.state = 'assigned'
            else:
                record.state = 'available'

    @api.model
    def get_dashboard_stats(self, category_id=None, date_start=None, date_end=None):
        """
        Returns stats for the dashboard with optional filtering
        """
        domain = []
        if category_id:
            domain.append(('category_id', '=', int(category_id)))
        
        if date_start:
            domain.append(('create_date', '>=', date_start))
        if date_end:
            domain.append(('create_date', '<=', date_end))

        # Basic Stats
        total_assets = self.search_count(domain)
        available = self.search_count(domain + [('state', '=', 'available')])
        assigned = self.search_count(domain + [('state', '=', 'assigned')])
        repair = self.search_count(domain + [('state', '=', 'repair')])
        retired = self.search_count(domain + [('state', '=', 'retired')])

        # Distribution by Category (for Bar Chart)
        categories = self.env['it_asset.category'].search([])
        category_data = []
        for cat in categories:
            cat_domain = [('category_id', '=', cat.id)]
            if date_start: cat_domain.append(('create_date', '>=', date_start))
            if date_end: cat_domain.append(('create_date', '<=', date_end))
            
            count = self.search_count(cat_domain)
            if count > 0 or not category_id: # Only show categories with data or if not filtering
                category_data.append({
                    'name': cat.name,
                    'count': count
                })

        # Distribution by State (for Pie Chart)
        state_data = [
            {'label': 'Available', 'value': available, 'color': '#22c55e'},
            {'label': 'Assigned', 'value': assigned, 'color': '#3b82f6'},
            {'label': 'Repair', 'value': repair, 'color': '#f59e0b'},
            {'label': 'Retired', 'value': retired, 'color': '#ef4444'}]

        return {
            'total_assets': total_assets,
            'available': available,
            'assigned': assigned,
            'repair': repair,
            'retired': retired,
            'category_distribution': sorted(category_data, key=lambda x: x['count'], reverse=True)[:10],
            'state_distribution': state_data,
        }
