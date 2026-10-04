import frappe
from frappe.utils.scheduler import enable_scheduler

def _create_rename_log_table():
    frappe.db.sql("""
        CREATE TABLE IF NOT EXISTS `tabCloud Rename Log` (
            `name` varchar(140) NOT NULL,
            `creation` datetime(6) DEFAULT NULL,
            `reference_doctype` varchar(140) DEFAULT NULL,
            `old_name` varchar(140) DEFAULT NULL,
            `new_name` varchar(140) DEFAULT NULL,
            PRIMARY KEY (`name`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """)
    frappe.db.commit()

def after_install():
    try:
        enable_scheduler()
    except Exception:
        pass
    _create_rename_log_table()

def after_migrate():
    try:
        enable_scheduler()
    except Exception:
        pass
    _create_rename_log_table()
