import json
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

class AttendancePushController(http.Controller):

    @http.route('/api/hr_attendance/push', type='json', auth='none', methods=['POST'], csrf=False)
    def push_attendance(self, **kwargs):
        """
        Endpoint to receive real-time attendance logs via POST JSON.
        Expected format:
        {
            "token": "YOUR_SECRET_TOKEN",
            "logs": [
                {
                    "user_id": 16,
                    "timestamp": "2026-03-03T14:50:21.000Z",
                    "type": 0,
                    "id": "289916",
                    "device_name": "Mesin Pintu Depan"
                }
            ]
        }
        """
        try:
            config = request.env['ir.config_parameter'].sudo()
            data = request.get_json_data()

            logs = data.get('logs', [])
            if not logs:
                return {"status": "error", "message": "No logs provided"}

            # Use the existing processing logic from the model
            type_in = int(config.get_param('hr_attendance_fingerprint.type_check_in') or 0)
            type_out = int(config.get_param('hr_attendance_fingerprint.type_check_out') or 1)
            api_tz_name = config.get_param('hr_attendance_fingerprint.api_timezone') or 'Asia/Makassar'

            request.env['hr.attendance'].sudo()._process_attendance_logs(logs, type_in, type_out, api_tz_name)
            
            return {"status": "success", "message": f"Processed {len(logs)} logs"}

        except Exception as e:
            _logger.error("Error in real-time attendance push: %s", str(e))
            return {"status": "error", "message": str(e)}
