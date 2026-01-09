{
    'name': 'IT Asset Management',
    'version': '1.0.0',
    'summary': 'Manage IT Assets PT GSI Site Wolo',
    'sequence': 1,
    'category': 'IT',
    'author': 'Your Name',
    'depends': ['base', 'product', 'hr', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'views/asset_category_views.xml',
        'views/asset_views.xml',
        'views/asset_assignment_views.xml',
        'views/asset_maintenance_views.xml',
    ],
    'installable': True,
    'application': True,
}
