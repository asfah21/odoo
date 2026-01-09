from odoo import models, fields

class ITDepartment(models.Model):
    _name = 'it.department'
    _description = 'IT Department'

    name = fields.Char(string='Department Name', required=True)
    code = fields.Char(string='Department Code')
    manager_id = fields.Many2one('hr.employee', string='Manager')
    note = fields.Text(string='Note')
