from odoo import models, fields, api, _

class ITConsumable(models.Model):
    _name = 'it_asset.consumable'
    _description = 'IT Consumable'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name asc'

    name = fields.Char(string='Consumable Name', required=True, tracking=True)
    product_id = fields.Many2one(
        'product.product', 
        string='Related Product', 
        domain=[('type', 'in', ['consu', 'product']), ('tracking', '=', 'none')],
        required=True,
        tracking=True
    )
    
    # Stock info (Read-only from Product)
    qty_available = fields.Float(
        'Quantity On Hand', 
        related='product_id.qty_available', 
        readonly=True, 
        store=False
    )
    uom_id = fields.Many2one(
        'uom.uom', 
        'Unit of Measure', 
        related='product_id.uom_id', 
        readonly=True
    )
    standard_price = fields.Float(
        'Cost', 
        related='product_id.standard_price', 
        readonly=True
    )

    min_quantity = fields.Integer(string='Minimum Quantity', default=5, help='Minimum quantity before restocking', tracking=True)
    description = fields.Text(string='Description')
    notes = fields.Html(string='Notes')

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id and not self.name:
            self.name = self.product_id.name

    def action_view_stock(self):
        self.ensure_one()
        try:
             # Try standard Odoo Reference first (usually product_open_quants or similar depending on version)
             # In Odoo 16/17/18 naming conventions can shift slightly. 
             # Let's use a safer approach: direct action definition or fallback.
             action = self.env.ref('stock.product_open_quants').read()[0]
        except ValueError:
             # Fallback for some Odoo versions where ID might differ
             action = self.env["ir.actions.actions"]._for_xml_id("stock.dashboard_open_quants")

        action['domain'] = [('product_id', '=', self.product_id.id)]
        action['context'] = {'default_product_id': self.product_id.id}
        return action
