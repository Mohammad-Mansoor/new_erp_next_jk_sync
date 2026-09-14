import frappe
import hmac
import hashlib
import time
import json

@frappe.whitelist(allow_guest=True)
def receive_sync_event():
    """
    Cloud endpoint to receive outbox events from branches.
    """
    try:
        # 1. Read headers
        branch_id = frappe.request.headers.get("X-Branch-ID")
        timestamp_str = frappe.request.headers.get("X-Timestamp")
        signature = frappe.request.headers.get("X-Signature")
        
        if not branch_id or not timestamp_str or not signature:
            frappe.local.response['http_status_code'] = 401
            return {"status": "PERMANENT_FAILED", "message": "Missing HMAC headers."}
            
        # 2. Replay Protection (5 minutes)
        try:
            timestamp = int(timestamp_str)
            if abs(time.time() - timestamp) > 300:
                frappe.local.response['http_status_code'] = 401
                return {"status": "PERMANENT_FAILED", "message": "Request timestamp expired."}
        except ValueError:
            frappe.local.response['http_status_code'] = 401
            return {"status": "PERMANENT_FAILED", "message": "Invalid timestamp format."}
            
        # 3. Authenticate Branch
        secret = frappe.db.get_value("Branch Sync Config", branch_id, "api_secret")
        if not secret:
            frappe.local.response['http_status_code'] = 401
            return {"status": "PERMANENT_FAILED", "message": "Branch ID not found or unauthorized."}
            
        payload_bytes = frappe.request.get_data()
        payload_str = payload_bytes.decode('utf-8')
        
        canonical = f"{branch_id}{timestamp_str}{payload_str}"
        expected_sig = hmac.new(
            secret.encode('utf-8'),
            canonical.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(expected_sig, signature):
            frappe.local.response['http_status_code'] = 401
            return {"status": "PERMANENT_FAILED", "message": "Invalid HMAC signature."}
            
        # 4. Parse Payload
        try:
            payload = json.loads(payload_str)
        except Exception:
            return {"status": "PERMANENT_FAILED", "message": "Invalid JSON."}
            
        event_id = payload.get("event_id")
        event_type = payload.get("event_type")
        owner = payload.get("owner")
        
        if not event_id or not event_type or not owner:
            return {"status": "PERMANENT_FAILED", "message": "Missing required payload fields."}
            
        # 5. User Authorization
        user_branch = frappe.db.get_value("User", owner, "branch_id")
        if user_branch != branch_id:
            frappe.local.response['http_status_code'] = 403
            return {"status": "PERMANENT_FAILED", "message": f"User {owner} is not authorized for branch {branch_id}."}
            
        # 6. Inbox Idempotency & Integrity Check
        payload_hash = hashlib.sha256(payload_str.encode('utf-8')).hexdigest()
        
        existing_inbox = frappe.db.get_value("Branch Sync Inbox", event_id, ["status", "payload_hash"], as_dict=True)
        if existing_inbox:
            if existing_inbox.payload_hash == payload_hash:
                return {"status": "DUPLICATE_ACK", "message": "Already processed successfully."}
            else:
                return {"status": "PERMANENT_FAILED", "message": "EVENT_ID_REUSE_WITH_DIFFERENT_PAYLOAD"}
                
        # 7. Business Processing
        frappe.set_user(owner)
        
        try:
            frappe.db.savepoint("sync_event_processing")
            
            if event_type == "POS Invoice":
                from jk_sync.handlers.pos_invoice import handle_pos_invoice
                handle_pos_invoice(payload)
            elif event_type == "POS Exchange":
                from jk_sync.handlers.pos_exchange import handle_pos_exchange
                handle_pos_exchange(payload)
            elif event_type == "POS Closing":
                from jk_sync.handlers.pos_closing import handle_pos_closing
                handle_pos_closing(payload)
            else:
                return {"status": "PERMANENT_FAILED", "message": f"Unsupported event_type: {event_type}"}
                
            # 8. Record in Inbox
            inbox_doc = frappe.get_doc({
                "doctype": "Branch Sync Inbox",
                "event_id": event_id,
                "payload_hash": payload_hash,
                "branch_id": branch_id,
                "status": "PROCESSED"
            })
            inbox_doc.insert(ignore_permissions=True)
            
            frappe.db.commit()
            return {"status": "PROCESSED", "message": "Success"}
            
        except frappe.exceptions.LinkValidationError as e:
            frappe.db.rollback(save_point="sync_event_processing")
            return {"status": "RETRYABLE_FAILED", "message": f"Missing Dependency: {str(e)}"}
        except frappe.exceptions.DoesNotExistError as e:
            frappe.db.rollback(save_point="sync_event_processing")
            return {"status": "RETRYABLE_FAILED", "message": f"Missing Record: {str(e)}"}
        except frappe.exceptions.DuplicateEntryError as e:
            frappe.db.rollback(save_point="sync_event_processing")
            return {"status": "PERMANENT_FAILED", "message": f"Business Identity Collision: {str(e)}"}
        except Exception as e:
            frappe.db.rollback(save_point="sync_event_processing")
            frappe.log_error(frappe.get_traceback(), f"Sync Error: {event_id}")
            return {"status": "PERMANENT_FAILED", "message": str(e)}
            
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Sync Receiver Fatal Error")
        return {"status": "RETRYABLE_FAILED", "message": "Internal Server Error"}
