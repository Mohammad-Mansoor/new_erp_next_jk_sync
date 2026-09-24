import frappe
from jk_sync.sync.outbox import enqueue_event

def is_branch_server():
    config = frappe.get_single("Branch Sync Config")
    return bool(config.cloud_url and config.branch_id)

def on_pos_invoice_submit(doc, method):
    if getattr(frappe.flags, "is_syncing", False) or not is_branch_server():
        return
    enqueue_event("POS Invoice", {"doc": doc.as_dict()})

def on_pos_exchange_submit(doc, method):
    if getattr(frappe.flags, "is_syncing", False) or not is_branch_server():
        return
    enqueue_event("POS Exchange", {"doc": doc.as_dict()})

def on_pos_opening_submit(doc, method):
    if getattr(frappe.flags, "is_syncing", False) or not is_branch_server():
        return
    enqueue_event("POS Opening", {"doc": doc.as_dict()})

def on_pos_closing_submit(doc, method):
    if getattr(frappe.flags, "is_syncing", False) or not is_branch_server():
        return
        
    # Optional: Find dependencies (POS Invoices in this closing)
    depends_on = []
    # We could parse the DB for tabBranch Sync Outbox where payload like %doc.name%,
    # but for safety and simplicity, we just enqueue it. The cloud can retry if needed.
    
    enqueue_event("POS Closing", {"doc": doc.as_dict()}, depends_on=depends_on)
