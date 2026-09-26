import frappe
from frappe.utils.scheduler import enable_scheduler

def after_install():
    try:
        enable_scheduler()
    except Exception:
        pass

def after_migrate():
    try:
        enable_scheduler()
    except Exception:
        pass
