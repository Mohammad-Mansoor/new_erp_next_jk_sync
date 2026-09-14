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
    
    # 1. Atomic Fenced Claim
    frappe.db.sql("""
        UPDATE `tabBranch Sync Outbox`
        SET status = 'PROCESSING',
            locked_by = %s,
            claim_token = %s,
            locked_at = NOW()
        WHERE status = 'PENDING'
        OR (status = 'PROCESSING' AND locked_at < NOW() - INTERVAL 5 MINUTE)
        LIMIT 50
    """, (worker_uuid, claim_token))
    frappe.db.commit()
    
    # 2. Retrieve claimed rows safely
    events = frappe.db.sql("""
        SELECT name, event_type, payload
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
    
    for event in events:
        # Renew lease before each heavy HTTP request just in case.
        affected = frappe.db.sql("""
            UPDATE `tabBranch Sync Outbox` SET locked_at = NOW() 
            WHERE name = %s AND claim_token = %s
        """, (event.name, claim_token))
        
        if affected == 0:
            # Lease was lost/expired and taken by another worker.
            continue
            
        frappe.db.commit()
        
        payload_json = event.payload
        # Inject event_id and event_type into payload for transport
        try:
            payload_dict = json.loads(payload_json)
            payload_dict["event_id"] = event.name
            payload_dict["event_type"] = event.event_type
            payload_json = json.dumps(payload_dict)
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

def mark_status(event_id, claim_token, status, error_log=None):
    frappe.db.sql("""
        UPDATE `tabBranch Sync Outbox`
        SET status = %s, error_log = %s
        WHERE name = %s AND claim_token = %s
    """, (status, error_log, event_id, claim_token))
    frappe.db.commit()

def enqueue_event(event_type, payload_dict):
    event_id = frappe.generate_hash(length=20)
    outbox_doc = frappe.get_doc({
        "doctype": "Branch Sync Outbox",
        "event_id": event_id,
        "event_type": event_type,
        "payload": json.dumps(payload_dict)
    })
    outbox_doc.insert(ignore_permissions=True)
    return event_id
