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
        type_in = int(config.get_param('hr_attendance_fingerprint.type_check_in') or 0)
        type_out = int(config.get_param('hr_attendance_fingerprint.type_check_out') or 1)
        
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
            result = response.json()
            
            _logger.info("Fingerprint API Response received. Total records: %s", result.get('total'))
            
            # The actual logs are in the 'rows' key based on provided JSON
            logs = []
            if isinstance(result, dict):
                if 'rows' in result:
                    logs = result['rows']
                elif 'data' in result:
                    logs = result['data']
                else:
                    _logger.warning("API response doesn't have 'rows' or 'data' keys. Using raw result if it's a list.")
                    if isinstance(result, list):
                        logs = result

            if not logs:
                _logger.info("No logs found in the API response.")
                return

            _logger.info("Processing %d logs from API", len(logs))
            self._process_attendance_logs(logs, type_in, type_out)
        except Exception as e:
            _logger.error("Failed to sync fingerprint attendance: %s", str(e))

    @api.model
    def _process_attendance_logs(self, logs, type_in=0, type_out=1):
        Employee = self.env['hr.employee'].sudo()
        Attendance = self.env['hr.attendance'].sudo()

        for log in logs:
            ext_id = str(log.get('external_log_id') or log.get('id') or '')
            fid = str(log.get('user_id') or '')
            log_time_str = log.get('timestamp') or log.get('datetime')
            raw_type = log.get('type') # 0=Check In, 1=Check Out
            device_name = log.get('device_name')
            device_sn = log.get('device_sn')

            _logger.info("Processing log entry: ext_id=%s, fid=%s, time=%s, type=%s", ext_id, fid, log_time_str, raw_type)

            if not ext_id or not fid or not log_time_str:
                _logger.warning("Skipping invalid log (missing required fields): %s", log)
                continue

            # Check if already exists to prevent duplication
            existing = Attendance.search([('external_log_id', '=', ext_id)], limit=1)
            if existing:
                _logger.info("Log with external_id %s already exists. Skipping.", ext_id)
                continue

            # Find employee by FID
            employee = Employee.search([('fid', '=', fid)], limit=1)
            if not employee:
                _logger.warning("Employee with FID %s not found in Odoo. Skipping log %s.", fid, ext_id)
                continue
            
            _logger.info("Matching employee found: %s (ID: %s) for FID: %s", employee.name, employee.id, fid)

            try:
                # Handle ISO 8601 format like "2026-03-02T14:21:02.000Z"
                # Odoo Datetime fields expect a naive UTC datetime object or a string in Odoo format
                if 'T' in log_time_str:
                    # Remove milliseconds and Z if present for simpler parsing if needed, 
                    # but dateutil or ISO format is better
                    log_time_str = log_time_str.replace('Z', '+00:00').split('.')[0]
                    check_time = datetime.strptime(log_time_str, '%Y-%m-%dT%H:%M:%S')
                else:
                    check_time = datetime.strptime(log_time_str, '%Y-%m-%d %H:%M:%S')
                
                _logger.info("Processing entry for employee %s at %s (Type: %s)", employee.name, check_time, raw_type)

                if raw_type == type_in: # Check In
                    Attendance.create({
                        'employee_id': employee.id,
                        'check_in': check_time,
                        'external_log_id': ext_id,
                        'device_name': device_name,
                        'device_sn': device_sn,
                        'raw_type': raw_type,
                    })
                    _logger.info("Created Check-In for %s", employee.name)
                elif raw_type == type_out: # Check Out
                    # Look for the last open attendance for this employee
                    open_attendance = Attendance.search([
                        ('employee_id', '=', employee.id),
                        ('check_out', '=', False)
                    ], order='check_in desc', limit=1)
                    
                    if open_attendance:
                        open_attendance.write({
                            'check_out': check_time,
                            'external_log_id': ext_id,
                            'device_name': device_name,
                            'device_sn': device_sn,
                            'raw_type': raw_type,
                        })
                        _logger.info("Updated Check-Out for %s", employee.name)
                    else:
                        # If no open check-in, create one with check_in = check_out
                        Attendance.create({
                            'employee_id': employee.id,
                            'check_in': check_time,
                            'check_out': check_time,
                            'external_log_id': ext_id,
                            'device_name': device_name,
                            'device_sn': device_sn,
                            'raw_type': raw_type,
                        })
                        _logger.info("Created direct Check-Out (no open In) for %s", employee.name)
                else:
                    _logger.info("Log raw_type %s does not match Check-In (%s) or Check-Out (%s) mapping. Skipping.", raw_type, type_in, type_out)
                
            except Exception as e:
                _logger.error("Error processing log %s: %s", ext_id, str(e))
