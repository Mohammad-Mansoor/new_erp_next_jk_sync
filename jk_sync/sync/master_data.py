import frappe
import requests
import json
import time
import hmac
import hashlib
import uuid

def log_master_data_rename(doc, method, old=None, new=None, merge=False):
    master_doctypes = [
        "Role", "Custom DocPerm", "Company", "Branch", "Department", "Designation", "Cost Center", 
        "Account", "Warehouse", "UOM", "Brand", "Tax Category", 
        "Item Tax Template", "Sales Taxes and Charges Template",
        "Item Group", "Customer Group", "Territory", "Mode of Payment", "POS Payment Method",
        "Currency", "Sales Person", "Loyalty Program", "Promotional Scheme", "Pricing Rule",
        "User", "Item", "Customer", "Address", "Contact", "Item Price", "POS Profile",
        "Print Format"
    ]
    if doc.doctype in master_doctypes:
        frappe.db.sql("""
            INSERT INTO `tabCloud Rename Log` (name, creation, reference_doctype, old_name, new_name)
            VALUES (%s, %s, %s, %s, %s)
        """, (str(uuid.uuid4())[:20], frappe.utils.now_datetime(), doc.doctype, old, new))

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

@frappe.whitelist()
def poll_master_data():
    """
    Background worker that runs on the branch to poll the Cloud for master data updates.
    Can also be called directly via RPC / UI button.
    Loops automatically until all pending master data batches are fetched.
    """
    config = frappe.get_single("Branch Sync Config")
    if not config.cloud_url or not config.api_key or not config.get_password("api_secret") or not config.branch_id:
        return {"status": "FAILED", "message": "Branch Sync Config parameters missing."}
        
    cloud_url = config.cloud_url.rstrip("/") + "/api/method/jk_sync.api.master.get_master_updates"
    
    total_records = 0
    iterations = 0
    max_iterations = 50  # Safety cap to prevent infinite loops
    
    while iterations < max_iterations:
        iterations += 1
        timestamp = str(int(time.time()))
        
        # Always reload fresh config to read updated last_master_data_sync timestamp
        config = frappe.get_single("Branch Sync Config")
        last_sync = config.last_master_data_sync or "2000-01-01 00:00:00"
        
        payload_json = json.dumps({"last_master_data_sync": str(last_sync)})
        canonical = f"{config.branch_id}{timestamp}{payload_json}"
        signature = hmac.new(
            config.get_password("api_secret").encode('utf-8'),
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
            response = requests.post(cloud_url, data=payload_json, headers=headers, timeout=60)
            if response.status_code != 200:
                return {"status": "FAILED", "message": f"Cloud returned HTTP {response.status_code}"}
                
            data = response.json().get("message", {})
            master_data = data.get("master_data", {})
            batch_count = sum(len(docs) for docs in master_data.values())
            total_records += batch_count
            
            process_master_updates(data)
            
            has_more = data.get("has_more", False)
            if not has_more or batch_count == 0:
                break
                
        except Exception as e:
            frappe.log_error(f"Polling Failed: {str(e)}", "Sync Polling Error")
            return {"status": "FAILED", "message": str(e)}
            
    return {
        "status": "SUCCESS", 
        "message": f"Master Data sync complete. Synced {total_records} records across {iterations} batch(es)."
    }

def _sync_child_tables_for_bypass(dt, doc_name, doc_dict):
    """
    Manually sync child tables when using db_insert/db_update bypass methods.
    """
    meta = frappe.get_meta(dt)
    for df in meta.get_table_fields():
        child_docs = doc_dict.get(df.fieldname, [])
        # Always wipe existing children for this parent/field to ensure exact mirror
        frappe.db.delete(df.options, {"parent": doc_name, "parentfield": df.fieldname})
        
        for i, child_dict in enumerate(child_docs):
            child_doc = frappe.new_doc(df.options)
            child_doc.update(child_dict)
            child_doc.parent = doc_name
            child_doc.parenttype = dt
            child_doc.parentfield = df.fieldname
            child_doc.idx = i + 1
            if not child_doc.name:
                child_doc.name = frappe.generate_hash(length=10)
            child_doc.db_insert()

def process_master_updates(data):
    """
    Process the downloaded master data locally.
    """
    # 0. Process Rename Logs (Must be done BEFORE master data updates)
    rename_logs = data.get("rename_logs", [])
    for rlog in rename_logs:
        # Ignore if we already processed this log (idempotency)
        if rlog.get("name") and frappe.db.exists("Cloud Rename Log", rlog.get("name")):
            continue
            
        local_log = frappe.new_doc("Cloud Rename Log")
        if rlog.get("name"):
            local_log.name = rlog.get("name")
        local_log.reference_doctype = rlog.get("reference_doctype")
        local_log.old_name = rlog.get("old_name")
        local_log.new_name = rlog.get("new_name")
        
        try:
            if frappe.db.exists(rlog.get("reference_doctype"), rlog.get("old_name")):
                frappe.rename_doc(rlog.get("reference_doctype"), rlog.get("old_name"), rlog.get("new_name"), merge=False, ignore_if_exists=True, force=True)
            local_log.status = "Success"
        except Exception:
            local_log.status = "Failed"
            local_log.error_log = frappe.get_traceback()
            frappe.log_error(local_log.error_log, f"Rename Failed {rlog.get('old_name')} -> {rlog.get('new_name')}")
            
        local_log.insert(ignore_permissions=True, set_name=rlog.get("name"))
        frappe.db.commit()

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
                        "basic_rate": 0,
                        "allow_zero_valuation_rate": 1
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
        "Role", "Custom DocPerm", "Company", "Branch", "Department", "Designation", "Cost Center", 
        "Account", "Warehouse", "UOM", "Brand", "Tax Category", 
        "Item Tax Template", "Sales Taxes and Charges Template",
        "Item Group", "Customer Group", "Territory", "Mode of Payment", "POS Payment Method",
        "Currency", "Sales Person", "Loyalty Program", "Promotional Scheme", "Pricing Rule",
        "User", "Notification Settings", "Item", "Customer", "Address", "Contact", "Item Price", "POS Profile",
        "Print Format"
    ]
    
    frappe.flags.is_syncing = True
    frappe.flags.in_import = True
    frappe.local.flags.ignore_chart_of_accounts = True
    frappe.local.flags.ignore_update_nsm = True
    
    for dt in master_doctypes:
        docs = master_data.get(dt, [])
        if not docs:
            continue
            
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
                # Handle User username collision
                if dt == "User":
                    incoming_username = doc_dict.get("username")
                    if incoming_username:
                        conflicting_user = frappe.db.get_value("User", {"username": incoming_username, "name": ("!=", doc_name)}, "name")
                        if conflicting_user:
                            import string, random
                            random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
                            frappe.db.set_value("User", conflicting_user, "username", f"{incoming_username}_{random_suffix}")
                            frappe.db.commit()

                meta = frappe.get_meta(dt)
                
                # 1. Pop out ALL child tables from doc_dict so Frappe's ORM never sees them
                child_tables_dict = {}
                for df in meta.get_table_fields():
                    if df.fieldname in doc_dict:
                        child_tables_dict[df.fieldname] = doc_dict.pop(df.fieldname)

                if frappe.db.exists(dt, doc_name):
                    doc = frappe.get_doc(dt, doc_name)
                    doc.update(doc_dict)
                    doc.flags.ignore_links = True
                    doc.flags.ignore_validate = True
                    doc.flags.ignore_mandatory = True
                    doc.flags.ignore_version = True
                    if dt == "Company":
                        doc.flags.ignore_chart_of_accounts = True
                    # Neuter Frappe python business logic completely
                    doc.run_before_save_methods = lambda *args, **kwargs: None
                    doc.run_post_save_methods = lambda *args, **kwargs: None
                    doc.run_method = lambda *args, **kwargs: None
                    
                    doc.db_update()
                    if dt == "Custom DocPerm":
                        frappe.clear_cache(doctype=doc.parent)
                else:
                    doc = frappe.get_doc(doc_dict)
                    doc.flags.ignore_links = True
                    doc.flags.ignore_validate = True
                    doc.flags.ignore_mandatory = True
                    doc.flags.ignore_version = True
                    if dt == "Company":
                        doc.flags.ignore_chart_of_accounts = True
                    # Neuter Frappe python business logic completely
                    doc.run_before_save_methods = lambda *args, **kwargs: None
                    doc.run_post_save_methods = lambda *args, **kwargs: None
                    doc.run_method = lambda *args, **kwargs: None
                    
                    doc.name = doc_name
                    doc.db_insert()
                        
                    if dt == "Custom DocPerm":
                        frappe.clear_cache(doctype=doc.parent)
                        
                # 2. UNIVERSAL CHILD TABLE INJECTOR
                for df in meta.get_table_fields():
                    child_docs = child_tables_dict.get(df.fieldname, [])
                    # Wipe existing children for this parent/field to ensure exact mirror
                    frappe.db.delete(df.options, {"parent": doc_name, "parentfield": df.fieldname})
                    
                    for i, child_dict in enumerate(child_docs):
                        child_doc = frappe.new_doc(df.options)
                        child_doc.update(child_dict)
                        child_doc.parent = doc_name
                        child_doc.parenttype = dt
                        child_doc.parentfield = df.fieldname
                        child_doc.idx = i + 1
                        if not child_doc.name:
                            child_doc.name = frappe.generate_hash(length=10)
                        child_doc.db_insert()
            except Exception as e:
                frappe.log_error(title="Master Data Sync Error", message=f"Failed to upsert {dt} {doc_name}: {str(e)}")
                
        # Rebuild tree structure for Tree DocTypes after importing batch
        if frappe.get_meta(dt).is_tree:
            try:
                parent_field = "parent_" + dt.lower().replace(" ", "_")
                from frappe.utils.nestedset import rebuild_tree
                rebuild_tree(dt, parent_field)
            except Exception as e:
                frappe.log_error(title="Tree Rebuild Error", message=f"Failed to rebuild tree for {dt}: {str(e)}")
                
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
        config.get_password("api_secret").encode('utf-8'),
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
    
    if config.get_password("api_secret"):
        signature = hmac.new(
            config.get_password("api_secret").encode('utf-8'),
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
                skipped_items = []
                for it in items:
                    if not frappe.db.exists("Item", it["item_code"]):
                        skipped_items.append(it["item_code"])
                        continue

                    items_list.append({
                        "item_code": it["item_code"],
                        "t_warehouse": wh,
                        "qty": abs(it["actual_qty"]),
                        "basic_rate": it.get("valuation_rate") or 0,
                        "allow_zero_valuation_rate": 1
                    })

                if not items_list:
                    continue
                    
                ste_args = {
                    "doctype": "Stock Entry",
                    "stock_entry_type": "Material Receipt",
                    "purpose": "Material Receipt",
                    "items": items_list
                }
                if wh_company:
                    ste_args["company"] = wh_company

                ste = frappe.get_doc(ste_args)

                # Identify inactive/end-of-life items and temporarily activate them
                revert_items = []
                for it in items_list:
                    item_doc = frappe.get_doc("Item", it["item_code"])
                    if item_doc.disabled or item_doc.end_of_life:
                        revert_items.append({
                            "item_code": item_doc.name,
                            "disabled": item_doc.disabled,
                            "end_of_life": item_doc.end_of_life
                        })
                        frappe.db.set_value("Item", item_doc.name, "disabled", 0)
                        frappe.db.set_value("Item", item_doc.name, "end_of_life", None)

                ste.insert(ignore_permissions=True)
                ste.submit()

                # Revert items back to their disabled/end-of-life state
                for rev in revert_items:
                    frappe.db.set_value("Item", rev["item_code"], "disabled", rev["disabled"])
                    frappe.db.set_value("Item", rev["item_code"], "end_of_life", rev["end_of_life"])

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

