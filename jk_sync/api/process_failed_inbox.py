import frappe
import json
from jk_sync.handlers.pos_invoice import handle_pos_invoice

def run():
    frappe.init(site="development.localhost")
    frappe.connect()
    
    inbox_records = frappe.db.sql("SELECT name, event_type, event_id FROM `tabBranch Sync Inbox` WHERE status = 'FAILED' LIMIT 4", as_dict=True)
    
    for r in inbox_records:
        if r.event_type == "POS Invoice":
            print(f"Checking {r.name}")
            try:
                # Wait, payload is not stored in the inbox...
                print(r)
            except Exception as e:
                print(e)
                
if __name__ == "__main__":
    run()
