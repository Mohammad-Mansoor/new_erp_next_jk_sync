import frappe
import hmac
import hashlib
import time
import json
import uuid

@frappe.whitelist(allow_guest=True)
def receive_sync_event():
    """
    Cloud endpoint to receive outbox events from branches.
    Implements a strict Atomic Insert Barrier to prevent concurrent execution.
    """
    try:
        # 1. Read headers
        branch_id = frappe.request.headers.get("X-Branch-ID")
        timestamp_str = frappe.request.headers.get("X-Timestamp")
        signature = frappe.request.headers.get("X-Signature")
        
        if not branch_id or not timestamp_str or not signature:
            frappe.local.response['http_status_code'] = 401
            return {"status": "PERMANENT_FAILED", "message": "Missing HMAC headers."}
            
        # 2. Replay Protection (24 hours to account for branch clock drift)
        try:
            timestamp = int(timestamp_str)
            if abs(time.time() - timestamp) > 86400:
                frappe.local.response['http_status_code'] = 401
                return {"status": "PERMANENT_FAILED", "message": "Request timestamp expired."}
        except ValueError:
            frappe.local.response['http_status_code'] = 401
            return {"status": "PERMANENT_FAILED", "message": "Invalid timestamp format."}
            
        # 3. Authenticate Branch
        secret = frappe.db.get_value("Cloud Branch Master", branch_id, "api_secret")
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
        if owner != "Administrator":
            user_branch = frappe.db.get_value("User", owner, "branch_id")
            if user_branch != branch_id:
                frappe.local.response['http_status_code'] = 403
                return {"status": "PERMANENT_FAILED", "message": f"User {owner} is not authorized for branch {branch_id}."}
            
        # 6. Inbox Idempotency - ATOMIC INSERT BARRIER
        payload_hash = hashlib.sha256(payload_str.encode('utf-8')).hexdigest()
        claim_token = str(uuid.uuid4())
        
        try:
            frappe.db.sql("""
                INSERT INTO `tabBranch Sync Inbox`
                (name, event_id, payload_hash, branch_id, status, claim_token, locked_at, creation, modified)
                VALUES (%s, %s, %s, %s, 'PROCESSING', %s, NOW(), NOW(), NOW())
            """, (event_id, event_id, payload_hash, branch_id, claim_token))
            frappe.db.commit() # Commit the lock immediately
        except Exception as e:
            if "1062" not in str(e) and "Duplicate" not in str(e):
                raise
                
            frappe.db.rollback() # Clear the failed insert state
            
            # Row exists. Let's acquire a row lock to inspect it safely.
            existing = frappe.db.sql("""
                SELECT status, payload_hash, locked_at
                FROM `tabBranch Sync Inbox` 
                WHERE event_id = %s 
                FOR UPDATE
            """, (event_id,), as_dict=True)
            
            if not existing:
                return {"status": "RETRYABLE_FAILED", "message": "Concurrency anomaly."}
                
            existing_doc = existing[0]
            
            if existing_doc.payload_hash != payload_hash:
                frappe.db.commit() # Release lock
                return {"status": "PERMANENT_FAILED", "message": "EVENT_ID_REUSE_WITH_DIFFERENT_PAYLOAD"}
                
            if existing_doc.status == "PROCESSED":
                frappe.db.commit() # Release lock
                return {"status": "DUPLICATE_ACK", "message": "Already processed successfully."}
                
            if existing_doc.status == "PROCESSING":
                # Is it an active claim or stale?
                # Check if locked_at is older than 5 minutes
                stale_threshold = frappe.db.sql("SELECT NOW() - INTERVAL 5 MINUTE")[0][0]
                
                if existing_doc.locked_at and existing_doc.locked_at > stale_threshold:
                    # Active claim
                    frappe.db.commit() # Release lock
                    return {"status": "RETRYABLE_FAILED", "message": "Event is currently processing by another worker."}
                else:
                    # Stale claim. Reclaim it with our claim_token!
                    frappe.db.sql("""
                        UPDATE `tabBranch Sync Inbox`
                        SET claim_token = %s, locked_at = NOW()
                        WHERE event_id = %s
                    """, (claim_token, event_id))
                    frappe.db.commit() # We now own the lease!
        
        # 7. Business Processing
        frappe.set_user(owner)
        
        try:
            # We own the Inbox row via claim_token.
            # Begin business transaction
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
            elif event_type == "POS Opening":
                from jk_sync.handlers.pos_opening import handle_pos_opening
                handle_pos_opening(payload)
            else:
                mark_inbox_failed(event_id, claim_token)
                return {"status": "PERMANENT_FAILED", "message": f"Unsupported event_type: {event_type}"}
                
            # 8. Mark PROCESSED
            affected = frappe.db.sql("""
                UPDATE `tabBranch Sync Inbox`
                SET status = 'PROCESSED'
                WHERE event_id = %s AND claim_token = %s
            """, (event_id, claim_token))
            
            if affected == 0:
                # Fencing token failed! Another worker stole our lease because we took too long.
                frappe.db.rollback(save_point="sync_event_processing")
                return {"status": "RETRYABLE_FAILED", "message": "Lost lease during execution."}
            
            frappe.db.commit()
            return {"status": "PROCESSED", "message": "Success"}
            
        except frappe.exceptions.LinkValidationError as e:
            frappe.db.rollback(save_point="sync_event_processing")
            mark_inbox_failed(event_id, claim_token) # For retryable we could just release lease, but marking FAILED is ok too. Actually, if we release the lease, the branch can retry immediately.
            return {"status": "RETRYABLE_FAILED", "message": f"Missing Dependency: {str(e)}"}
        except frappe.exceptions.DoesNotExistError as e:
            frappe.db.rollback(save_point="sync_event_processing")
            mark_inbox_failed(event_id, claim_token)
            frappe.log_error(frappe.get_traceback(), f"Missing Record: {str(e)}")
            return {"status": "RETRYABLE_FAILED", "message": f"Missing Record: {str(e)}"}
        except frappe.exceptions.DuplicateEntryError as e:
            frappe.db.rollback(save_point="sync_event_processing")
            mark_inbox_failed(event_id, claim_token)
            return {"status": "PERMANENT_FAILED", "message": f"Business Identity Collision: {str(e)}"}
        except Exception as e:
            frappe.db.rollback(save_point="sync_event_processing")
            frappe.log_error(frappe.get_traceback(), f"Sync Error: {event_id}")
            mark_inbox_failed(event_id, claim_token)
            return {"status": "PERMANENT_FAILED", "message": str(e)}
            
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Sync Receiver Fatal Error")
        return {"status": "RETRYABLE_FAILED", "message": "Internal Server Error"}

def mark_inbox_failed(event_id, claim_token):
    frappe.db.sql("""
        UPDATE `tabBranch Sync Inbox`
        SET status = 'FAILED'
        WHERE event_id = %s AND claim_token = %s
    """, (event_id, claim_token))
    frappe.db.commit()
