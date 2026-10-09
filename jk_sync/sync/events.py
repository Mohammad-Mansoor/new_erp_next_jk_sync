import frappe
from jk_sync.sync.outbox import enqueue_event

def is_branch_server():
    config = frappe.get_single("Branch Sync Config")
    return bool(config.cloud_url and config.branch_id)

def autoname_with_branch(doc, method):
    if getattr(frappe.flags, "is_syncing", False) or not is_branch_server():
        return
        
    config = frappe.get_single("Branch Sync Config")
    branch_id = config.branch_id
    if not branch_id:
        return
        
    # Generate the standard name that Frappe would have generated
    from frappe.model.naming import set_name_by_naming_series, make_autoname
    
    max_retries = 1000
    previous_raw_name = None
    
    for _ in range(max_retries):
        doc.name = None
        
        # 1. Try to set via naming_series field
        try:
            set_name_by_naming_series(doc)
        except Exception:
            pass
            
        # 2. If doc.name is still not set, fallback to the DocType's default autoname metadata
        if not doc.name:
            autoname_setting = frappe.get_meta(doc.doctype).autoname
            if autoname_setting:
                doc.name = make_autoname(autoname_setting)
                
        if not doc.name:
            return
            
        raw_name = str(doc.name)
        
        # If the generated name didn't change from the last loop, it means this is a static name 
        # (like field-based naming) and NOT a sequence. We must break to avoid infinite loops.
        if raw_name == previous_raw_name:
            break
            
        previous_raw_name = raw_name
            
        # 3. Prepend the branch_id if it's not already there
        prefix = f"{branch_id}-"
        if not str(doc.name).startswith(prefix):
            doc.name = f"{prefix}{doc.name}"
            
        # 4. Check if this prefixed name already exists in the local database
        if not frappe.db.exists(doc.doctype, doc.name):
            return  # Success! Found a unique slot.
            
    # If it exhausts 1000 retries or breaks from static names, it will just exit the loop
    # and Frappe will throw a Duplicate Name error natively if it tries to insert.

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
