import frappe

def run():
    frappe.init(site="development.localhost")
    frappe.connect()
    for dt in ["POS Opening Entry", "POS Closing Entry", "POS Invoice", "POS Exchange"]:
        try:
            print(f"{dt}: {frappe.get_meta(dt).autoname}")
        except Exception:
            pass
