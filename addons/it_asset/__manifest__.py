{
    'name': 'IT Department',
    'version': '1.0.0',
    'summary': 'Centralized IT management for PT GSI – site Wolo',
    'sequence': 1,
    'category': 'IT',
    'author': 'Azvan',
    'depends': ['base', 'product', 'hr', 'stock', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'views/asset_views.xml',
        'views/dashboard_views.xml',
        'views/asset_category_views.xml',
        'views/asset_assignment_views.xml',
        'views/asset_maintenance_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'it_asset/static/src/components/dashboard/dashboard.js',
            'it_asset/static/src/components/dashboard/dashboard.xml',
            'it_asset/static/src/components/dashboard/dashboard.scss',
        ],
        'web.assets_frontend': [
            'it_asset/static/src/scss/login_style.scss',
        ],
    },
    'installable': True,
    'application': True,
}
