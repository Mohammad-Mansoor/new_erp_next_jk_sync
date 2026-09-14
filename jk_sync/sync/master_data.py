import frappe
import requests
import json
import time
import hmac
import hashlib

def customer_before_rename(doc, method, old, new, merge=False):
    if not merge:
        return
        
    old_sync_id = frappe.db.get_value("Customer", old, "customer_sync_id")
    new_sync_id = frappe.db.get_value("Customer", new, "customer_sync_id")
    
    if not old_sync_id or not new_sync_id:
        return
        
    # Check for cycles
    if frappe.db.exists("Customer Merge Log", {"new_customer_uuid": old_sync_id, "old_customer_uuid": new_sync_id}):
        frappe.throw("Merge Cycle Detected. Cannot merge back to original customer.")
        
    merge_log = frappe.get_doc({
        "doctype": "Customer Merge Log",
        "old_customer_uuid": old_sync_id,
        "new_customer_uuid": new_sync_id
    })
    merge_log.insert(ignore_permissions=True)

def poll_master_data():
    """
    Background worker that runs on the branch to poll the Cloud for master data updates.
    """
    config = frappe.get_single("Branch Sync Config")
    if not config.cloud_url or not config.api_key or not config.api_secret or not config.branch_id:
        return
        
    # Ask the Cloud for master data updates
    cloud_url = config.cloud_url.rstrip("/") + "/api/method/jk_sync.api.master.get_master_updates"
    
    timestamp = str(int(time.time()))
    payload_json = json.dumps({"last_sync": "2020-01-01"}) # Simplified for POC
    canonical = f"{config.branch_id}{timestamp}{payload_json}"
    signature = hmac.new(
        config.api_secret.encode('utf-8'),
        canonical.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    headers = {
        "X-Branch-ID": config.branch_id,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(cloud_url, data=payload_json, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json().get("message", {})
            process_master_updates(data)
    except Exception as e:
        frappe.log_error(f"Polling Failed: {str(e)}", "Sync Polling Error")

def process_master_updates(data):
    """
    Process the downloaded master data locally.
    """
    # 1. Processing Merged Customers locally
    merge_logs = data.get("customer_merges", [])
    for merge in merge_logs:
        old_uuid = merge.get("old_customer_uuid")
        new_uuid = merge.get("new_customer_uuid")
        
        old_name = frappe.db.get_value("Customer", {"customer_sync_id": old_uuid}, "name")
        new_name = frappe.db.get_value("Customer", {"customer_sync_id": new_uuid}, "name")
        
        if old_name and new_name and old_name != new_name:
            try:
                frappe.rename_doc("Customer", old_name, new_name, merge=True)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"Offline Merge failed {old_name} -> {new_name}")

    # 2. Process Stock Deltas
    stock_deltas = data.get("stock_deltas", [])
    if stock_deltas:
        for delta in stock_deltas:
            # We natively generate a Material Receipt or Issue locally.
            try:
                purpose = "Material Receipt" if delta["qty_change"] > 0 else "Material Issue"
                ste = frappe.get_doc({
                    "doctype": "Stock Entry",
                    "stock_entry_type": purpose,
                    "purpose": purpose,
                    "items": [{
                        "item_code": delta["item_code"],
                        "t_warehouse": delta["warehouse"] if delta["qty_change"] > 0 else None,
                        "s_warehouse": delta["warehouse"] if delta["qty_change"] < 0 else None,
                        "qty": abs(delta["qty_change"]),
                        "basic_rate": 0 # Handled by valuation if needed
                    }]
                })
                # Prevent this local SLE from infinitely syncing back to the cloud.
                # (Normally the cloud hook blocks POS Closing Entries, but this is a Stock Entry).
                # The cloud already has this stock, so we don't need this to sync up.
                ste.insert(ignore_permissions=True)
                ste.submit()
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"Failed to inject stock delta for {delta.get('item_code')}")
