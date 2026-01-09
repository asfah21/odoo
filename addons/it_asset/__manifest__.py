{
    'name': 'IT Asset Management',
    'version': '1.0.0',
    'summary': 'Manage IT Assets',
    'category': 'IT',
    'depends': ['base', 'product', 'hr', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'views/asset_action.xml',
        'views/asset_view.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': True
}
