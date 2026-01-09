{
    'name': 'IT Department Asset',
    'version': '1.0.0',
    'summary': 'Manage IT Assets within IT Department',
    'category': 'IT',
    'author': 'Your Name',
    'depends': ['base', 'product', 'hr', 'stock', 'it_department'],
    'data': [
        'security/ir.model.access.csv',
        'views/asset_view.xml',
    ],
    'installable': True,
    'application': False,
}
