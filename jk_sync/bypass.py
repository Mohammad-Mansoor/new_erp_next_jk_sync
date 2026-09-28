import frappe
def bypass():
    frappe.db.set_value("System Settings", "System Settings", "setup_complete", 1)
    frappe.db.commit()
    print("Setup Wizard Bypassed!")
