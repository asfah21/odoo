BEST PRACTICE ODOO STRUCTURE

odoo/addons/
├── it_department
│   ├── models/
│   │   └── department.py
│   ├── views/
│   │   └── department_view.xml
│   └── __manifest__.py
│
├── it_department_asset
│   ├── models/
│   │   └── asset.py
│   ├── views/
│   │   └── asset_view.xml
│   ├── security/
│   │   └── ir.model.access.csv
│   └── __manifest__.py
│
└── it_asset (LEGACY, sudah on di server namun akan dipindahkan ke it_department_asset agar lebih scalable)