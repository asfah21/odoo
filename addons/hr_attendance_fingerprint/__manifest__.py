{
    'name': 'HR Attendance Fingerprint Sync',
    'version': '1.0',
    'category': 'Human Resources/Attendances',
    'summary': 'Sync fingerprint data from external API to Odoo Attendances',
    'description': """
        This module inherits:
        - hr.employee to add Fingerprint ID (FID)
        - hr.attendance to add external metadata
        - Settings to configure API Endpoint
        - Scheduled action to sync data
    """,
    'author': 'Antigravity',
    'depends': ['hr_attendance'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/hr_employee_views.xml',
        'views/hr_attendance_views.xml',
        'data/ir_cron_data.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
