import frappe

def run():
    frappe.init(site="development.localhost")
    frappe.connect()
    frappe.db.sql("UPDATE `tabBranch Sync Inbox` SET status = 'FAILED' WHERE status = 'PROCESSING'")
    frappe.db.commit()
    print("Fixed stuck inbox records.")

if __name__ == "__main__":
    run()
