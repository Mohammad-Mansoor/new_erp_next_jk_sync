import frappe

def handle_pos_opening(payload):
    """
    Handles a synced POS Opening Entry event.
    """
    doc_dict = payload.get("doc")
    if not doc_dict:
        frappe.throw("Payload missing 'doc' key for POS Opening.")
        
    doc_dict.pop("modified", None)
    
    doc = frappe.get_doc(doc_dict)
    doc.insert(set_name=True, ignore_permissions=True)
    doc.submit()
