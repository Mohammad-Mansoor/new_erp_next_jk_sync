import frappe

def run():
    frappe.init(site="development.localhost")
    frappe.connect()

    # Simulate being a local branch server by temporarily setting config
    config = frappe.get_single("Branch Sync Config")
    config.cloud_url = "http://test.com"
    config.branch_id = "BR99"
    
    doc = frappe.new_doc("POS Opening Entry")
    doc.company = "Jahan Kodak"
    doc.period_start_date = "2026-01-01 00:00:00"
    doc.pos_profile = "Kabul Cash Register"
    doc.user = "Administrator"
    
    # Fire autoname manually since we don't want to actually insert
    doc.run_method("autoname")
    print(f"Generated Name: {doc.name}")

if __name__ == "__main__":
    run()
