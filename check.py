import frappe

def run():
    frappe.init(site="development.localhost")
    frappe.connect()
    for dt in ["POS Opening Entry", "POS Closing Entry", "POS Invoice"]:
        print(f"{dt}: {frappe.get_meta(dt).autoname}")

if __name__ == "__main__":
    run()
