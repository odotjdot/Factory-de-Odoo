{
    "name": 'FMOS FAM Bridge',
    "summary": "Company-isolated bidirectional FMOS/Odoo bridge",
    "version": '19.0.1.0.0',
    "author": 'Funk Media',
    "license": 'LGPL-3',
    "category": "Technical",
    "depends": ['base'],
    "data": ['security/groups.xml', 'security/ir.model.access.csv', 'security/record_rules.xml', 'data/cron.xml', 'views/bridge_record_views.xml', 'views/action_receipt_views.xml', 'views/command_outbox_views.xml', 'views/menu.xml'],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
