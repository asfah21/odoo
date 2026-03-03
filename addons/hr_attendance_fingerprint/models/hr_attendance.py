from odoo import models, fields, api, _
import requests
import logging
from datetime import datetime
import pytz

_logger = logging.getLogger(__name__)

class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    external_log_id = fields.Char(string='External Log ID', index=True, help="Original Log ID")
    ext_in_id = fields.Char(string='External In ID', index=True)
    ext_out_id = fields.Char(string='External Out ID', index=True)
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
        api_tz_name = config.get_param('hr_attendance_fingerprint.api_timezone') or 'Asia/Makassar'
        
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
            self._process_attendance_logs(logs, type_in, type_out, api_tz_name)
        except Exception as e:
            _logger.error("Failed to sync fingerprint attendance: %s", str(e))

    @api.model
    def _process_attendance_logs(self, logs, type_in=0, type_out=1, api_tz_name='Asia/Makassar'):
        Employee = self.env['hr.employee'].sudo()
        Attendance = self.env['hr.attendance'].sudo()
        
        api_tz = pytz.timezone(api_tz_name)

        # Sort logs by timestamp ascending to process 'In' before 'Out'
        logs.sort(key=lambda x: x.get('timestamp') or x.get('datetime') or '')

        for log in logs:
            ext_id = str(log.get('external_log_id') or log.get('id') or '')
            fid = str(log.get('user_id') or '')
            log_time_str = log.get('timestamp') or log.get('datetime')
            raw_type = log.get('type') # 0=Check In, 1=Check Out
            device_name = log.get('device_name')
            device_sn = log.get('device_sn')

            if not ext_id or not fid or not log_time_str:
                _logger.warning("Skipping invalid log (missing required fields): %s", log)
                continue

            # Check if this log has already been processed
            existing = Attendance.search([
                '|', ('external_log_id', '=', ext_id),
                '|', ('ext_in_id', '=', ext_id),
                ('ext_out_id', '=', ext_id)
            ], limit=1)
            
            if existing:
                continue

            # Find employee by FID
            employee = Employee.search([('fid', '=', fid)], limit=1)
            if not employee:
                continue

            try:
                # 1. Parse string to naive datetime
                # Handle ISO 8601 format and fallback
                if 'T' in log_time_str:
                    log_time_cleaned = log_time_str.replace('T', ' ').replace('Z', '').split('.')[0]
                    naive_time = datetime.strptime(log_time_cleaned, '%Y-%m-%d %H:%M:%S')
                else:
                    naive_time = datetime.strptime(log_time_str, '%Y-%m-%d %H:%M:%S')
                
                # 2. Convert from API Timezone to UTC for Odoo storage
                # We assume the time in API is LOCAL time (e.g. 14:50 WITA)
                local_time = api_tz.localize(naive_time)
                check_time = local_time.astimezone(pytz.UTC).replace(tzinfo=None)

                _logger.info("Syncing %s: API Time %s (%s) -> Odoo UTC %s", 
                             employee.name, naive_time, api_tz_name, check_time)

                if raw_type == type_in: # Check In
                    # Avoid creating multiple open attendances
                    open_attendance = Attendance.search([
                        ('employee_id', '=', employee.id),
                        ('check_out', '=', False)
                    ], limit=1)
                    
                    if not open_attendance:
                        Attendance.create({
                            'employee_id': employee.id,
                            'check_in': check_time,
                            'external_log_id': ext_id,
                            'ext_in_id': ext_id,
                            'device_name': device_name,
                            'device_sn': device_sn,
                            'raw_type': raw_type,
                        })
                        _logger.info("Created Check-In for %s at %s UTC", employee.name, check_time)

                elif raw_type == type_out: # Check Out
                    # Pair with the FIRST open attendance of the day (asc)
                    open_attendance = Attendance.search([
                        ('employee_id', '=', employee.id),
                        ('check_out', '=', False)
                    ], order='check_in asc', limit=1)
                    
                    if open_attendance:
                        # Ensure check_out is after check_in
                        if check_time >= open_attendance.check_in:
                            open_attendance.write({
                                'check_out': check_time,
                                'ext_out_id': ext_id,
                            })
                            _logger.info("Updated Check-Out for %s at %s UTC", employee.name, check_time)
                        else:
                            open_attendance = False

                    if not open_attendance:
                        # Create direct Check-Out record
                        Attendance.create({
                            'employee_id': employee.id,
                            'check_in': check_time,
                            'check_out': check_time,
                            'external_log_id': ext_id,
                            'ext_out_id': ext_id,
                            'device_name': device_name,
                            'device_sn': device_sn,
                            'raw_type': raw_type,
                        })
                        _logger.info("Created direct Check-Out for %s at %s UTC", employee.name, check_time)

                else:
                    _logger.info("Log raw_type %s does not match mapping. Skipping log %s.", raw_type, ext_id)
                
            except Exception as e:
                _logger.error("Error processing log %s: %s", ext_id, str(e))
                self.env.cr.rollback() # Rollback current entry transaction if failed

