from odoo import models, fields

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    fid = fields.Char(string='Fingerprint ID', help="Mapping to user_id from Fingerprint API")
