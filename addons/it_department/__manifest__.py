{
    'name': 'IT Department Core',
    'version': '1.0.0',
    'summary': 'Core module for IT Department management',
    'category': 'IT',
    'author': 'Your Name',
    'depends': ['base', 'hr'],
    'data': [
        'security/ir.model.access.csv',
        'views/department_view.xml',
    ],
    'installable': True,
    'application': True,
}
