import frappe

def handle_pos_exchange(payload):
    """
    Handles a synced POS Exchange event.
    For simplicity, assume POS Exchange just submits a document, or multiple documents if it's a composite payload.
    """
    doc_dict = payload.get("doc")
    if not doc_dict:
        frappe.throw("Payload missing 'doc' key for POS Exchange.")
        
    # Usually, a POS return is just a POS Invoice with is_return=1.
    # We resolve the canonical customer just like POS Invoice.
    from jk_sync.handlers.pos_invoice import resolve_canonical_customer
    resolve_canonical_customer(doc_dict)
    
    doc_dict.pop("modified", None)
    
    doc = frappe.get_doc(doc_dict)
    doc.insert(set_name=True, ignore_permissions=True)
    doc.submit()
