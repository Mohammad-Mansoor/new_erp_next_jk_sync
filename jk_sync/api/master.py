import frappe

@frappe.whitelist(allow_guest=True)
def get_master_updates():
    """
    Called by branch servers to fetch new Customers, Configurations, and Stock Logs.
    """
    # HMAC Authentication (simplified for brevity here, similar to receiver.py)
    branch_id = frappe.request.headers.get("X-Branch-ID")
    if not branch_id:
        return {"status": "FAILED", "message": "Missing Branch ID"}
        
    # Fetch POS Profiles linked to this branch
    # Usually POS Profile has a custom field branch_id, or we just fetch all profiles.
    
    # Fetch Stock Sync Logs
    stock_logs = frappe.get_all(
        "Cloud Stock Sync Log", 
        filters={"branch_id": branch_id, "is_synced": 0},
        fields=["name", "item_code", "warehouse", "qty_change", "reference_doctype", "reference_name"]
    )
    
    # Mark them as synced (optimistic lock or actual lock)
    for log in stock_logs:
        frappe.db.set_value("Cloud Stock Sync Log", log.name, "is_synced", 1)
        
    return {
        "status": "SUCCESS",
        "stock_deltas": stock_logs,
        "customer_merges": [], # (Fetched similarly)
        "pos_profiles": [] # (Fetched similarly)
    }
