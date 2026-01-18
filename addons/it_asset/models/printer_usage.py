from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class ITPrinterUsage(models.Model):
    _name = 'it_asset.printer.usage'
    _description = 'Printer Usage Tracking'
    _order = 'date desc, id desc'

    asset_id = fields.Many2one('it_asset.asset', string='Printer', required=True, 
                               domain="[('category_id.name', 'ilike', 'Printer')]",
                               ondelete='cascade')
    date = fields.Date(string='Reading Date', required=True, default=fields.Date.context_today)
    color_pages = fields.Integer(string='Color Pages', default=0)
    bw_pages = fields.Integer(string='B/W Pages', default=0)
    total_pages = fields.Integer(string='Current Counter (Total)', compute='_compute_total_pages', store=True)
    pages_diff = fields.Integer(string='Pages Printed (Diff)', compute='_compute_pages_diff', store=True)
    bw_diff = fields.Integer(string='B/W Printed (Diff)', compute='_compute_pages_diff', store=True)
    color_diff = fields.Integer(string='Color Printed (Diff)', compute='_compute_pages_diff', store=True)
    remarks = fields.Char(string='Remarks')

    @api.depends('color_pages', 'bw_pages')
    def _compute_total_pages(self):
        for record in self:
            record.total_pages = record.color_pages + record.bw_pages

    @api.depends('asset_id', 'date', 'total_pages', 'color_pages', 'bw_pages')
    def _compute_pages_diff(self):
        for record in self:
            prev_usage = self.search([
                ('asset_id', '=', record.asset_id.id),
                ('date', '<=', record.date),
                ('id', '!=', record.id)
            ], order='date desc, id desc', limit=1)
            
            if prev_usage:
                record.pages_diff = record.total_pages - prev_usage.total_pages
                record.bw_diff = record.bw_pages - prev_usage.bw_pages
                record.color_diff = record.color_pages - prev_usage.color_pages
            else:
                record.pages_diff = 0
                record.bw_diff = 0
                record.color_diff = 0

    @api.constrains('total_pages')
    def _check_counter_logic(self):
        for record in self:
            prev_usage = self.search([
                ('asset_id', '=', record.asset_id.id),
                ('date', '<', record.date)
            ], order='date desc', limit=1)
            if prev_usage and record.total_pages < prev_usage.total_pages:
                raise ValidationError(_("Counter value cannot be less than the previous reading (%s pages).") % prev_usage.total_pages)
