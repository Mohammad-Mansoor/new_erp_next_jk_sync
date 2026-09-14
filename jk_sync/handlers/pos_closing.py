import frappe

def handle_pos_closing(payload):
    """
    Handles a synced POS Closing Entry event.
    """
    doc_dict = payload.get("doc")
    if not doc_dict:
        frappe.throw("Payload missing 'doc' key for POS Closing.")
        
    doc_dict.pop("modified", None)
    
    doc = frappe.get_doc(doc_dict)
    doc.insert(set_name=True, ignore_permissions=True)
    
    # Submitting the POS Closing Entry naturally causes ERPNext to 
    # generate the consolidated Sales Invoice, GL Entries, and Stock Ledger Entries.
    doc.submit()
