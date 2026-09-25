import frappe

def handle_pos_invoice(payload):
    """
    Inserts a POS Invoice synced from a branch into the Cloud DB.
    """
    doc_dict = payload.get("doc")
    if not doc_dict:
        frappe.throw("Payload missing 'doc' key for POS Invoice.")
        
    resolve_canonical_customer(doc_dict)
    
    # Clean up standard fields that shouldn't be blindly inserted
    doc_dict.pop("modified", None)
    
    doc = frappe.get_doc(doc_dict)
    # Important: set_name=True ensures we use the exact name (e.g. POS-BR01-0001) from the branch
    doc.insert(set_name=doc.name, set_child_names=False, ignore_permissions=True)
    doc.submit()

def resolve_canonical_customer(doc_dict):
    """
    Looks up the customer in the Merge Log.
    If the customer was merged, rewrites the doc_dict to use the surviving canonical customer.
    """
    customer = doc_dict.get("customer")
    if not customer:
        return
        
    if frappe.db.exists("Customer", customer):
        return
        
    # Customer doesn't exist. Maybe they were merged?
    # We need to map the customer name -> sync_id -> new_sync_id -> new_customer_name.
    # Since the branch sent the customer 'name' (e.g. "John Doe"), we need to know its UUID.
    # The branch should technically send the customer_sync_id in the payload for safety.
    # Let's assume the branch injected customer_sync_id into the payload:
    sync_id = doc_dict.get("customer_sync_id")
    if not sync_id:
        frappe.throw(f"Customer {customer} not found and no customer_sync_id provided for resolution.")
        
    # Check merge log recursively (in case of A -> B -> C)
    current_uuid = sync_id
    while True:
        log = frappe.db.get_value("Customer Merge Log", {"old_customer_uuid": current_uuid}, "new_customer_uuid")
        if not log:
            break
        current_uuid = log
        
    canonical_customer = frappe.db.get_value("Customer", {"customer_sync_id": current_uuid}, "name")
    if canonical_customer:
        doc_dict["customer"] = canonical_customer
        # If there's a contact or address, those might need resolution too, but
        # standard POS usually just links customer.
    else:
        frappe.throw(f"Customer {customer} could not be resolved canonically.")
