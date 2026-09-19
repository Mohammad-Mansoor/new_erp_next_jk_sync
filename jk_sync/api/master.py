import frappe
import json

@frappe.whitelist(allow_guest=True)
def get_master_updates():
    """
    Called by branch servers to fetch new Customers, Configurations, and Stock Logs.
    """
    branch_id = frappe.request.headers.get("X-Branch-ID")
    if not branch_id:
        return {"status": "FAILED", "message": "Missing Branch ID"}
        
    try:
        payload = json.loads(frappe.request.get_data())
    except Exception:
        payload = {}
        
    last_sync = payload.get("last_master_data_sync")
    if not last_sync:
        last_sync = "2000-01-01 00:00:00"

    # Master DocTypes in dependency order
    master_doctypes = [
        "Role", "Company", "Cost Center", "Account", "Warehouse", "UOM", 
        "Item Group", "Customer Group", "Mode of Payment", "POS Payment Method",
        "User", "Item", "Customer", "Item Price", "POS Profile"
    ]
    
    master_data_payload = {}
    
    for dt in master_doctypes:
        if not frappe.db.exists("DocType", dt):
            continue
            
        records = frappe.get_all(
            dt, 
            filters={"modified": (">", last_sync)},
            order_by="modified asc",
            limit=500
        )
        
        doc_list = []
        for r in records:
            try:
                doc = frappe.get_doc(dt, r.name)
                if dt == "User":
                    # Passwords are not stored in the document directly anyway (in __Auth)
                    pass
                doc_list.append(doc.as_dict())
            except Exception as e:
                frappe.log_error(f"Error serializing {dt} {r.name}: {str(e)}", "Master Sync Expansion Error")
                
        if doc_list:
            master_data_payload[dt] = doc_list
            
    # Fetch Stock Sync Logs
    stock_logs = frappe.get_all(
        "Cloud Stock Sync Log", 
        filters={"branch_id": branch_id, "is_synced": 0},
        fields=["name", "item_code", "warehouse", "qty_change", "reference_doctype", "reference_name"]
    )
    
    for log in stock_logs:
        frappe.db.set_value("Cloud Stock Sync Log", log.name, "is_synced", 1)
        
    return {
        "status": "SUCCESS",
        "stock_deltas": stock_logs,
        "customer_merges": [], 
        "master_data": master_data_payload
    }
