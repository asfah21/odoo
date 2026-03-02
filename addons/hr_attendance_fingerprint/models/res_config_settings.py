from odoo import models, fields

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    fingerprint_api_url = fields.Char(
        string='Fingerprint API URL',
        config_parameter='hr_attendance_fingerprint.api_url',
        help="Endpoint URL to fetch attendance logs (JSON format)"
    )
