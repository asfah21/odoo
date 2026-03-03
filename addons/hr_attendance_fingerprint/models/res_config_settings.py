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
    fingerprint_type_check_in = fields.Integer(
        string='Check-In Type Value',
        default=0,
        config_parameter='hr_attendance_fingerprint.type_check_in',
        help="The 'type' value from API that represents a Check-In"
    )
    fingerprint_type_check_out = fields.Integer(
        string='Check-Out Type Value',
        default=1,
        config_parameter='hr_attendance_fingerprint.type_check_out',
        help="The 'type' value from API that represents a Check-Out"
    )
    fingerprint_api_timezone = fields.Selection(
        [
            ('UTC', 'UTC'),
            ('Asia/Jakarta', 'WIB (GMT+7)'),
            ('Asia/Makassar', 'WITA (GMT+8)'),
            ('Asia/Jayapura', 'WIT (GMT+9)'),
        ],
        string='API Timezone',
        default='Asia/Makassar',
        config_parameter='hr_attendance_fingerprint.api_timezone',
        help="The timezone used by the Fingerprint API. Use Asia/Makassar for WITA."
    )
    fingerprint_sync_interval = fields.Integer(
        string='Sync Interval Value',
        default=1,
        config_parameter='hr_attendance_fingerprint.sync_interval',
        help="Number of units for the sync interval"
    )
    fingerprint_sync_interval_type = fields.Selection(
        [
            ('minutes', 'Minutes'),
            ('hours', 'Hours'),
            ('days', 'Days'),
        ],
        string='Sync Interval Type',
        default='hours',
        config_parameter='hr_attendance_fingerprint.sync_interval_type',
        help="Unit of time for the sync interval"
    )

    def set_values(self):

        super(ResConfigSettings, self).set_values()
        # Update the cron interval
        sync_cron = self.env.ref('hr_attendance_fingerprint.ir_cron_sync_fingerprint', raise_if_not_found=False)
        if sync_cron:
            sync_cron.sudo().write({
                'interval_number': self.fingerprint_sync_interval,
                'interval_type': self.fingerprint_sync_interval_type,
            })

