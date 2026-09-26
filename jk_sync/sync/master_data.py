import frappe
import requests
import json
import time
import hmac
import hashlib
import uuid

def customer_before_rename(doc, method, old, new, merge=False):
    if not merge:
        return
        
    old_sync_id = frappe.db.get_value("Customer", old, "customer_sync_id")
    new_sync_id = frappe.db.get_value("Customer", new, "customer_sync_id")
    
    if not old_sync_id or not new_sync_id:
        return
        
    # Check for cycles (Deep Traversal)
    current_canonical = new_sync_id
    visited = set([new_sync_id])
    
    while current_canonical:
        # If the canonical resolves back to old_sync_id, we have a cycle!
        if current_canonical == old_sync_id:
            frappe.throw("Merge Cycle Detected. Cannot merge back to an ancestor.")
            
        # Find next hop
        next_canonical = frappe.db.get_value("Customer Merge Log", {"old_customer_uuid": current_canonical}, "new_customer_uuid")
        if not next_canonical:
            break
            
        if next_canonical in visited:
            frappe.throw("Corrupted existing merge cycle detected in database.")
            
        visited.add(next_canonical)
        current_canonical = next_canonical
        
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
    last_sync = config.last_master_data_sync or "2000-01-01 00:00:00"
    payload_json = json.dumps({"last_master_data_sync": str(last_sync)})
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

    # 2. Process Stock Deltas (Idempotent Pull Protocol)
    stock_deltas = data.get("stock_deltas", [])
    if stock_deltas:
        # Sort by sequence_no if available to ensure ordering
        stock_deltas = sorted(stock_deltas, key=lambda x: x.get("name")) # Cloud Stock Sync Log name is autoincrement
        
        for delta in stock_deltas:
            stock_delta_id = delta.get("name")
            claim_token = str(uuid.uuid4())
            
            # Atomic Idempotency Barrier
            try:
                frappe.db.sql("""
                    INSERT INTO `tabLocal Stock Sync Log`
                    (name, stock_delta_id, status, claim_token, locked_at, item_code, warehouse, qty_change, creation, modified)
                    VALUES (%s, %s, 'PROCESSING', %s, NOW(), %s, %s, %s, NOW(), NOW())
                """, (stock_delta_id, stock_delta_id, claim_token, delta.get("item_code"), delta.get("warehouse"), delta.get("qty_change")))
                frappe.db.commit() # Acquire lock
            except Exception as e:
                if "1062" not in str(e) and "Duplicate" not in str(e):
                    raise
                frappe.db.rollback()
                # Delta already processed or processing
                existing = frappe.db.sql("SELECT status FROM `tabLocal Stock Sync Log` WHERE stock_delta_id = %s FOR UPDATE", (stock_delta_id,), as_dict=True)
                if existing and existing[0].status == "PROCESSED":
                    frappe.db.commit()
                    ack_stock_delta(stock_delta_id) # Just in case the ACK was lost previously
                    continue
                frappe.db.commit()
                continue
                
            try:
                frappe.db.savepoint("stock_delta")
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
                        "basic_rate": 0 
                    }]
                })
                # We tell the Cloud Hook (if any) to ignore this via flags, but this is a local insert.
                frappe.flags.is_syncing = True
                ste.insert(ignore_permissions=True)
                ste.submit()
                frappe.flags.is_syncing = False
                
                # Mark PROCESSED
                frappe.db.sql("UPDATE `tabLocal Stock Sync Log` SET status = 'PROCESSED' WHERE stock_delta_id = %s AND claim_token = %s", (stock_delta_id, claim_token))
                frappe.db.commit()
                
                # Send ACK back to Cloud
                ack_stock_delta(stock_delta_id)
                
            except Exception:
                frappe.db.rollback(save_point="stock_delta")
                frappe.db.sql("UPDATE `tabLocal Stock Sync Log` SET status = 'FAILED' WHERE stock_delta_id = %s AND claim_token = %s", (stock_delta_id, claim_token))
                frappe.db.commit()
                frappe.log_error(frappe.get_traceback(), f"Failed to inject stock delta {stock_delta_id}")

    # 3. Process Master Data
    master_data = data.get("master_data", {})
    master_doctypes = [
        "Role", "Company", "Branch", "Department", "Designation", "Cost Center", 
        "Account", "Warehouse", "UOM", "Brand", "Tax Category", 
        "Item Tax Template", "Sales Taxes and Charges Template",
        "Item Group", "Customer Group", "Territory", "Mode of Payment", "POS Payment Method",
        "Currency", "Sales Person", "Loyalty Program", "Promotional Scheme", "Pricing Rule",
        "User", "Item", "Customer", "Address", "Contact", "Item Price", "POS Profile",
        "Print Format"
    ]
    
    frappe.flags.is_syncing = True
    frappe.flags.in_import = True
    
    for dt in master_doctypes:
        docs = master_data.get(dt, [])
        
        if frappe.get_meta(dt).is_tree:
            docs = sorted(docs, key=lambda x: x.get("lft") or 0)
            
        for doc_dict in docs:
            doc_name = doc_dict.get("name")
            
            # Prevent Frappe ORM TimestampMismatch errors by removing cloud timestamps
            doc_dict.pop("modified", None)
            doc_dict.pop("_original_modified", None)
            doc_dict.pop("creation", None)
            doc_dict.pop("owner", None)
            doc_dict.pop("modified_by", None)
            
            try:
                if frappe.db.exists(dt, doc_name):
                    doc = frappe.get_doc(dt, doc_name)
                    doc.update(doc_dict)
                    doc.flags.ignore_links = True
                    doc.flags.ignore_validate = True
                    doc.flags.ignore_mandatory = True
                    # Neuter Frappe python business logic
                    doc.validate = lambda *args, **kwargs: None
                    doc.before_save = lambda *args, **kwargs: None
                    doc.on_update = lambda *args, **kwargs: None
                    doc.save(ignore_permissions=True)
                else:
                    doc = frappe.get_doc(doc_dict)
                    doc.flags.ignore_links = True
                    doc.flags.ignore_validate = True
                    doc.flags.ignore_mandatory = True
                    # Neuter Frappe python business logic
                    doc.validate = lambda *args, **kwargs: None
                    doc.before_insert = lambda *args, **kwargs: None
                    doc.before_save = lambda *args, **kwargs: None
                    doc.on_update = lambda *args, **kwargs: None
                    doc.insert(set_name=doc_name, ignore_permissions=True)
            except Exception as e:
                frappe.log_error(title="Master Data Sync Error", message=f"Failed to upsert {dt} {doc_name}: {str(e)}")
                
    frappe.flags.is_syncing = False
    
    # Update last_sync timestamp if we processed any master data payload
    if master_data:
        safe_next_sync = data.get("next_sync_timestamp")
        if not safe_next_sync:
            safe_next_sync = frappe.utils.now_datetime()
            
        frappe.db.set_value("Branch Sync Config", "Branch Sync Config", "last_master_data_sync", safe_next_sync)

def ack_stock_delta(stock_delta_id):
    """
    Send acknowledgement back to Cloud that the delta was durably processed.
    """
    config = frappe.get_single("Branch Sync Config")
    if not config.cloud_url:
        return
        
    cloud_url = config.cloud_url.rstrip("/") + "/api/method/jk_sync.api.master.ack_stock_delta"
    
    timestamp = str(int(time.time()))
    payload_json = json.dumps({"stock_delta_id": stock_delta_id})
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
        requests.post(cloud_url, data=payload_json, headers=headers, timeout=10)
    except Exception:
        pass # If ACK fails, it's fine. Cloud will resend and we will idempotently handle it.

@frappe.whitelist()
def sync_opening_stock_from_cloud():
    """
    Whitelisted method triggered by the UI button on Branch Sync Config.
    Fetches opening stock snapshot from Cloud and creates a local Stock Entry.
    """
    config = frappe.get_single("Branch Sync Config")
    if not config.cloud_url or not config.branch_id:
        frappe.throw("Cloud URL and Branch ID must be configured in Branch Sync Config first.")
        
    cloud_url = config.cloud_url.rstrip("/") + "/api/method/jk_sync.api.master.get_opening_stock_snapshot"
    
    timestamp = str(int(time.time()))
    payload_json = json.dumps({})
    canonical = f"{config.branch_id}{timestamp}{payload_json}"
    
    headers = {
        "X-Branch-ID": config.branch_id,
        "X-Timestamp": timestamp,
        "Content-Type": "application/json"
    }
    
    if config.api_secret:
        signature = hmac.new(
            config.api_secret.encode('utf-8'),
            canonical.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        headers["X-Signature"] = signature
        
    try:
        response = requests.post(cloud_url, data=payload_json, headers=headers, timeout=30)
        if response.status_code != 200:
            return {"status": "FAILED", "message": f"Cloud returned HTTP {response.status_code}: {response.text}"}
            
        data = response.json().get("message", {})
        if data.get("status") != "SUCCESS":
            return {"status": "FAILED", "message": data.get("message", "Cloud error fetching snapshot")}
            
        snapshot = data.get("stock_snapshot", [])
        if not snapshot:
            return {"status": "SUCCESS", "message": "No available opening stock found on Cloud for this branch."}
            
        by_warehouse = {}
        for item in snapshot:
            wh = item.get("warehouse")
            by_warehouse.setdefault(wh, []).append(item)
            
        created_entries = []
        frappe.flags.is_syncing = True
        
        try:
            for wh, items in by_warehouse.items():
                wh_company = frappe.db.get_value("Warehouse", wh, "company")
                if not wh_company:
                    companies = frappe.get_all("Company", pluck="name")
                    wh_company = companies[0] if companies else None

                items_list = []
                for it in items:
                    items_list.append({
                        "item_code": it["item_code"],
                        "t_warehouse": wh,
                        "qty": abs(it["actual_qty"]),
                        "basic_rate": 0
                    })
                    
                ste_args = {
                    "doctype": "Stock Entry",
                    "stock_entry_type": "Material Receipt",
                    "purpose": "Material Receipt",
                    "items": items_list
                }
                if wh_company:
                    ste_args["company"] = wh_company

                ste = frappe.get_doc(ste_args)
                ste.insert(ignore_permissions=True)
                ste.submit()
                created_entries.append(ste.name)

                
            frappe.db.commit()
            return {
                "status": "SUCCESS",
                "message": f"Successfully created local Opening Stock Entries ({', '.join(created_entries)}) for {len(snapshot)} items."
            }
        finally:
            frappe.flags.is_syncing = False
            
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Opening Stock Sync Error")
        return {"status": "FAILED", "message": f"Error syncing opening stock: {str(e)}"}

