import frappe

def run():
    frappe.init(site="development.localhost")
    frappe.connect()

    if not frappe.db.exists("Cloud Branch Master", "BR02"):
        doc = frappe.get_doc({
            "doctype": "Cloud Branch Master",
            "branch_id": "BR02",
            "api_key": "test_key",
            "api_secret": "test_secret"
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        print("Created Cloud Branch Master for BR02")
    else:
        print("BR02 already exists")

if __name__ == "__main__":
    run()
