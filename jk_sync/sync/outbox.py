import frappe
import uuid
import requests
import hmac
import hashlib
import time
import json

def process_outbox():
    """
    Background worker method called every minute to process the Outbox.
    """
    worker_uuid = frappe.generate_hash(length=12)
    claim_token = str(uuid.uuid4())
    
    # 1. Atomic Fenced Claim (with dependency check)
    # We only claim PENDING rows if all their dependencies are PROCESSED.
    # In MariaDB, we can do a subquery or we just claim PENDING, then check dependencies in python,
    # and if not ready, release them.
    # Since MariaDB 10.6 JSON functions in WHERE clauses in UPDATE statements can be complex,
    # let's do Python-side dependency filtering for safety.
    
    frappe.db.sql("""
        UPDATE `tabBranch Sync Outbox`
        SET status = 'PROCESSING',
            locked_by = %s,
            claim_token = %s,
            locked_at = NOW()
        WHERE status IN ('PENDING', 'RETRYABLE_FAILED')
        OR (status = 'PROCESSING' AND locked_at < NOW() - INTERVAL 5 MINUTE)
        LIMIT 50
    """, (worker_uuid, claim_token))
    frappe.db.commit()
    
    # 2. Retrieve claimed rows safely
    events = frappe.db.sql("""
        SELECT name, event_type, payload, depends_on, owner
        FROM `tabBranch Sync Outbox`
        WHERE status = 'PROCESSING' 
        AND locked_by = %s 
        AND claim_token = %s
    """, (worker_uuid, claim_token), as_dict=True)
    
    if not events:
        return
        
    config = frappe.get_single("Branch Sync Config")
    if not config.cloud_url or not config.api_key or not config.api_secret or not config.branch_id:
        frappe.db.sql("UPDATE `tabBranch Sync Outbox` SET status='PENDING', locked_at=NULL WHERE claim_token=%s", (claim_token,))
        frappe.db.commit()
        return
        
    cloud_url = config.cloud_url.rstrip("/") + "/api/method/jk_sync.api.receiver.receive_sync_event"
    
    # 1. Fast-Fail Circuit Breaker (Ping)
    try:
        # Lightweight request with strict 3-second timeout to check internet connectivity
        ping_url = config.cloud_url.rstrip("/")
        requests.head(ping_url, timeout=3)
    except requests.exceptions.RequestException:
        # Network is down. Return claimed events gracefully to RETRYABLE_FAILED and abort instantly.
        frappe.db.sql("UPDATE `tabBranch Sync Outbox` SET status='RETRYABLE_FAILED', locked_at=NULL WHERE claim_token=%s", (claim_token,))
        frappe.db.commit()
        return
    
    for event in events:
        # Dependency check
        if event.depends_on:
            try:
                deps = json.loads(event.depends_on)
                if deps:
                    unmet = frappe.db.sql("""
                        SELECT name FROM `tabBranch Sync Outbox` 
                        WHERE name IN %s AND status != 'PROCESSED'
                    """, (tuple(deps),))
                    if unmet:
                        # Dependency not ready! Release claim and set status to DEPENDENCY_NOT_READY (or just PENDING)
                        # The prompt says "If a dependency is RETRYABLE_FAILED, dependent event remains waiting. 
                        # If a dependency is PERMANENT_FAILED, make the dependency failure visible."
                        
                        failed_deps = frappe.db.sql("""
                            SELECT name FROM `tabBranch Sync Outbox`
                            WHERE name IN %s AND status = 'PERMANENT_FAILED'
                        """, (tuple(deps),))
                        
                        if failed_deps:
                            mark_status(event.name, claim_token, "PERMANENT_FAILED", f"Dependency permanently failed: {failed_deps[0][0]}")
                        else:
                            mark_status(event.name, claim_token, "PENDING", "Waiting for dependencies to process.")
                        continue
            except Exception:
                pass # Invalid depends_on json
                
        # Renew lease before each heavy HTTP request just in case.
        affected = frappe.db.sql("""
            UPDATE `tabBranch Sync Outbox` SET locked_at = NOW() 
            WHERE name = %s AND claim_token = %s
        """, (event.name, claim_token))
        
        if affected == 0:
            continue
            
        frappe.db.commit()
        
        payload_json = event.payload
        try:
            payload_dict = json.loads(payload_json)
            payload_dict["event_id"] = event.name
            payload_dict["event_type"] = event.event_type
            payload_dict["owner"] = event.owner
            payload_json = frappe.as_json(payload_dict)
        except Exception:
            mark_status(event.name, claim_token, "PERMANENT_FAILED", "Invalid JSON payload.")
            continue
            
        timestamp = str(int(time.time()))
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
                resp_data = response.json().get("message", {})
                if resp_data.get("status") in ("PROCESSED", "DUPLICATE_ACK"):
                    mark_status(event.name, claim_token, "SUCCESS", "Processed successfully.")
                elif resp_data.get("status") == "RETRYABLE_FAILED":
                    mark_status(event.name, claim_token, "RETRYABLE_FAILED", resp_data.get("message"))
                elif resp_data.get("status") == "PERMANENT_FAILED":
                    mark_status(event.name, claim_token, "PERMANENT_FAILED", resp_data.get("message"))
                else:
                    mark_status(event.name, claim_token, "RETRYABLE_FAILED", f"Unknown response: {response.text}")
            elif response.status_code in (401, 403):
                mark_status(event.name, claim_token, "PERMANENT_FAILED", f"Auth Error: {response.text}")
            else:
                mark_status(event.name, claim_token, "RETRYABLE_FAILED", f"HTTP {response.status_code}: {response.text}")
                
        except requests.exceptions.RequestException as e:
            mark_status(event.name, claim_token, "RETRYABLE_FAILED", f"Network Error: {str(e)}")
            
            # 2. Fast-Break Loop
            # If the internet drops mid-batch, instantly abort the rest of the 50 items 
            # to prevent 25 minutes of hanging timeouts.
            frappe.db.sql("UPDATE `tabBranch Sync Outbox` SET status='RETRYABLE_FAILED', locked_at=NULL WHERE claim_token=%s AND status='PROCESSING'", (claim_token,))
            frappe.db.commit()
            break
            
def mark_status(event_id, claim_token, status, error_log=None):
    if status == "SUCCESS":
        status = "PROCESSED" # Normalize to doctype option
        
    frappe.db.sql("""
        UPDATE `tabBranch Sync Outbox`
        SET status = %s, error_log = %s
        WHERE name = %s AND claim_token = %s
    """, (status, error_log, event_id, claim_token))
    frappe.db.commit()

def enqueue_event(event_type, payload_dict, depends_on=None):
    event_id = frappe.generate_hash(length=20)
    doc_args = {
        "doctype": "Branch Sync Outbox",
        "event_id": event_id,
        "event_type": event_type,
        "payload": frappe.as_json(payload_dict)
    }
    if depends_on:
        doc_args["depends_on"] = json.dumps(depends_on)
        
    outbox_doc = frappe.get_doc(doc_args)
    outbox_doc.insert(ignore_permissions=True)
    return event_id
