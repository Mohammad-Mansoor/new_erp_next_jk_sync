import frappe

def get_errors():
    errors = frappe.db.sql("SELECT method, error, creation FROM `tabError Log` WHERE error LIKE '%Account%' OR error LIKE '%Cost Center%' OR error LIKE '%Warehouse%' ORDER BY creation DESC LIMIT 10", as_dict=True)
    for e in errors:
        print("==========")
        print(f"Method: {e.method}")
        print(f"Creation: {e.creation}")
        print(f"Error: {e.error}")
