from odoo import models, fields

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    fingerprint_api_url = fields.Char(
        string='Fingerprint API URL',
        config_parameter='hr_attendance_fingerprint.api_url',
        help="Endpoint URL to fetch attendance logs (JSON format)"
    )
    fingerprint_api_key = fields.Char(
        string='Fingerprint API Key',
        config_parameter='hr_attendance_fingerprint.api_key',
        help="API Key value for authentication"
    )
    fingerprint_api_key_header = fields.Char(
        string='API Key Header Name',
        default='x-api-key',
        config_parameter='hr_attendance_fingerprint.api_key_header',
        help="The header name for the API Key (e.g., x-api-key, Authorization, etc.)"
    )
