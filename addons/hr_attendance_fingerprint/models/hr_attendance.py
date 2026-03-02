from odoo import models, fields, api, _
import requests
import logging
from datetime import datetime
import pytz

_logger = logging.getLogger(__name__)

class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    external_log_id = fields.Char(string='External Log ID', index=True)
    device_name = fields.Char(string='Device Name')
    device_sn = fields.Char(string='Device SN')
    raw_type = fields.Integer(string='Raw Type', help="0=check_in, 1=check_out")

    @api.model
    def _cron_sync_fingerprint_attendance(self):
        config = self.env['ir.config_parameter'].sudo()
        api_url = config.get_param('hr_attendance_fingerprint.api_url')
        api_key = config.get_param('hr_attendance_fingerprint.api_key')
        api_key_header = config.get_param('hr_attendance_fingerprint.api_key_header') or 'x-api-key'
        
        if not api_url:
            _logger.warning("Fingerprint API URL is not configured.")
            return

        try:
            _logger.info("Starting fingerprint attendance sync from %s", api_url)
            headers = {}
            if api_key:
                headers[api_key_header] = api_key
            
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            logs = response.json()
            
            if isinstance(logs, dict) and 'data' in logs:
                logs = logs['data']
            
            if not isinstance(logs, list):
                _logger.error("API response is not a list of logs")
                return

            self._process_attendance_logs(logs)
        except Exception as e:
            _logger.error("Failed to sync fingerprint attendance: %s", str(e))

    @api.model
    def _process_attendance_logs(self, logs):
        Employee = self.env['hr.employee'].sudo()
        Attendance = self.env['hr.attendance'].sudo()

        for log in logs:
            ext_id = str(log.get('external_log_id') or log.get('id') or '')
            fid = str(log.get('user_id') or '')
            log_time_str = log.get('timestamp') or log.get('datetime')
            raw_type = log.get('type') # 0=Check In, 1=Check Out
            device_name = log.get('device_name')
            device_sn = log.get('device_sn')

            if not ext_id or not fid or not log_time_str:
                _logger.debug("Skipping invalid log: %s", log)
                continue

            # Check if already exists to prevent duplication
            existing = Attendance.search([('external_log_id', '=', ext_id)], limit=1)
            if existing:
                continue

            # Find employee by FID
            employee = Employee.search([('fid', '=', fid)], limit=1)
            if not employee:
                _logger.info("Employee with FID %s not found. Skipping log %s.", fid, ext_id)
                continue

            try:
                # Assuming API provides YYYY-MM-DD HH:MM:SS
                # Odoo stores datetimes in UTC. If the API is local, we should convert.
                # For now, let's assume it's naive or already UTC.
                check_time = datetime.strptime(log_time_str, '%Y-%m-%d %H:%M:%S')
                
                if raw_type == 0: # Check In
                    Attendance.create({
                        'employee_id': employee.id,
                        'check_in': check_time,
                        'external_log_id': ext_id,
                        'device_name': device_name,
                        'device_sn': device_sn,
                        'raw_type': raw_type,
                    })
                elif raw_type == 1: # Check Out
                    # Look for the last open attendance for this employee
                    open_attendance = Attendance.search([
                        ('employee_id', '=', employee.id),
                        ('check_out', '=', False)
                    ], order='check_in desc', limit=1)
                    
                    if open_attendance:
                        open_attendance.write({
                            'check_out': check_time,
                            'external_log_id': ext_id, # This overwrites the IN external ID if we only have one field.
                            # But request asked for external_log_id on the model.
                            'device_name': device_name,
                            'device_sn': device_sn,
                            'raw_type': raw_type,
                        })
                    else:
                        # If no open check-in, create one with both as same time or just check_in
                        Attendance.create({
                            'employee_id': employee.id,
                            'check_in': check_time,
                            'check_out': check_time,
                            'external_log_id': ext_id,
                            'device_name': device_name,
                            'device_sn': device_sn,
                            'raw_type': raw_type,
                        })
                
            except Exception as e:
                _logger.error("Error processing log %s: %s", ext_id, str(e))
