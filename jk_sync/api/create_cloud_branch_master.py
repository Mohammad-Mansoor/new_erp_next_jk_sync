import frappe

def create_doctype():
    frappe.init(site="development.localhost")
    frappe.connect()

    doctype_name = "Cloud Branch Master"

    if frappe.db.exists("DocType", doctype_name):
        print(f"DocType {doctype_name} already exists.")
        return

    doc = frappe.get_doc({
        "doctype": "DocType",
        "name": doctype_name,
        "module": "Jahan Kodak Sync",
        "custom": 1,
        "autoname": "field:branch_id",
        "fields": [
            {
                "fieldname": "branch_id",
                "fieldtype": "Data",
                "label": "Branch ID",
                "reqd": 1,
                "unique": 1,
                "in_list_view": 1
            },
            {
                "fieldname": "api_key",
                "fieldtype": "Data",
                "label": "API Key",
                "reqd": 1,
                "in_list_view": 1
            },
            {
                "fieldname": "api_secret",
                "fieldtype": "Password",
                "label": "API Secret",
                "reqd": 1
            }
        ],
        "permissions": [
            {
                "role": "System Manager",
                "read": 1,
                "write": 1,
                "create": 1,
                "delete": 1
            }
        ]
    })
    
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"Created DocType {doctype_name}")

if __name__ == "__main__":
    create_doctype()
