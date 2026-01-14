from odoo import models, fields, api

class ITConsumable(models.Model):
    _name = 'it_asset.consumable'
    _description = 'IT Consumable'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name asc'

    name = fields.Char(string='Consumable Name', required=True, tracking=True)
    product_id = fields.Many2one('product.product', string='Related Product', domain=[('detailed_type', '=', 'consu')], tracking=True)
    quantity = fields.Integer(string='Quantity On Hand', default=0, tracking=True)
    min_quantity = fields.Integer(string='Minimum Quantity', default=5, help='Minimum quantity before restocking', tracking=True)
    description = fields.Text(string='Description')

    notes = fields.Html(string='Notes')
