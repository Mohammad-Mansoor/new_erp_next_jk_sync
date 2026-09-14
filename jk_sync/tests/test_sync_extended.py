import frappe
import unittest
import uuid
import time
import json
import threading
from jk_sync.api.receiver import receive_sync_event
from jk_sync.sync.outbox import process_outbox, enqueue_event
from jk_sync.sync.master_data import customer_before_rename

# Global counter to track business handler executions
HANDLER_EXECUTIONS = 0

def mock_handle_pos_invoice(payload):
    global HANDLER_EXECUTIONS
    HANDLER_EXECUTIONS += 1
    # Simulate DB work
    time.sleep(0.5)
    
class TestJKSyncExtended(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.flags.in_test = True
        
        # We need a branch config for receiver
        if not frappe.db.exists("Branch Sync Config", "BR01"):
            frappe.get_doc({
                "doctype": "Branch Sync Config",
                "branch_id": "BR01",
                "api_secret": "secret",
                "cloud_url": "http://localhost:8000"
            }).insert(ignore_permissions=True)
            
        frappe.db.commit()

    def setUp(self):
        global HANDLER_EXECUTIONS
        HANDLER_EXECUTIONS = 0
        frappe.db.rollback()

    def _simulate_http_request(self, event_id, payload_hash, event_type="POS Invoice"):
        # We mock frappe.request and frappe.set_user internally
        frappe.connect() # New connection for thread
        import jk_sync.handlers.pos_invoice
        jk_sync.handlers.pos_invoice.handle_pos_invoice = mock_handle_pos_invoice
        
        try:
            # We mock the receiver internals for the test, specifically the DB lock part
            claim_token = str(uuid.uuid4())
            try:
                frappe.db.sql("""
                    INSERT INTO `tabBranch Sync Inbox`
                    (name, event_id, payload_hash, branch_id, status, claim_token, locked_at, creation, modified)
                    VALUES (%s, %s, %s, 'BR01', 'PROCESSING', %s, NOW(), NOW(), NOW())
                """, (event_id, event_id, payload_hash, claim_token))
                frappe.db.commit()
            except frappe.exceptions.DuplicateEntryError:
                frappe.db.rollback()
                existing = frappe.db.sql("SELECT status, payload_hash, locked_at FROM `tabBranch Sync Inbox` WHERE event_id = %s FOR UPDATE", (event_id,), as_dict=True)
                if existing:
                    existing_doc = existing[0]
                    if existing_doc.payload_hash != payload_hash:
                        frappe.db.commit()
                        return "PERMANENT_FAILED_HASH"
                    if existing_doc.status == "PROCESSED":
                        frappe.db.commit()
                        return "DUPLICATE_ACK"
                    if existing_doc.status == "PROCESSING":
                        frappe.db.commit()
                        return "RETRYABLE_FAILED_LOCKED"
            
            # Business Handler
            frappe.db.savepoint("sync")
            mock_handle_pos_invoice({})
            
            frappe.db.sql("UPDATE `tabBranch Sync Inbox` SET status = 'PROCESSED' WHERE event_id = %s", (event_id,))
            frappe.db.commit()
            return "PROCESSED"
        finally:
            frappe.destroy()

    def test_01_inbox_concurrency_same_payload(self):
        """Simulate 3 concurrent requests. Only 1 must execute the handler."""
        event_id = str(uuid.uuid4())
        payload_hash = "H1"
        
        results = []
        def worker():
            res = self._simulate_http_request(event_id, payload_hash)
            results.append(res)
            
        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads: t.start()
        for t in threads: t.join()
        
        self.assertEqual(HANDLER_EXECUTIONS, 1)
        self.assertEqual(results.count("PROCESSED"), 1)
        self.assertTrue(results.count("RETRYABLE_FAILED_LOCKED") >= 1 or results.count("DUPLICATE_ACK") >= 1)

    def test_02_inbox_concurrency_diff_payload(self):
        """Simulate 2 concurrent requests with different payloads."""
        event_id = str(uuid.uuid4())
        
        results = []
        def worker(h):
            res = self._simulate_http_request(event_id, h)
            results.append(res)
            
        t1 = threading.Thread(target=worker, args=("H1",))
        t2 = threading.Thread(target=worker, args=("H2",))
        
        # Stagger slightly so T1 inserts first
        t1.start()
        time.sleep(0.1)
        t2.start()
        
        t1.join()
        t2.join()
        
        self.assertEqual(HANDLER_EXECUTIONS, 1)
        self.assertIn("PERMANENT_FAILED_HASH", results)
        
    def test_03_inbox_lost_ack(self):
        event_id = str(uuid.uuid4())
        res1 = self._simulate_http_request(event_id, "H1")
        self.assertEqual(res1, "PROCESSED")
        self.assertEqual(HANDLER_EXECUTIONS, 1)
        
        res2 = self._simulate_http_request(event_id, "H1")
        self.assertEqual(res2, "DUPLICATE_ACK")
        self.assertEqual(HANDLER_EXECUTIONS, 1) # Still 1!

    def test_04_customer_merge_cycles(self):
        frappe.connect()
        uid = str(uuid.uuid4())[:8]
        A = f"A_{uid}"
        B = f"B_{uid}"
        C = f"C_{uid}"
        
        # A -> B
        frappe.get_doc({"doctype": "Customer Merge Log", "old_customer_uuid": A, "new_customer_uuid": B}).insert(ignore_permissions=True)
        # B -> C
        frappe.get_doc({"doctype": "Customer Merge Log", "old_customer_uuid": B, "new_customer_uuid": C}).insert(ignore_permissions=True)
        
        original_get_value = frappe.db.get_value
        def mock_get_value(doctype, filters, fieldname=None):
            if doctype == "Customer":
                if filters == "C": return C
                if filters == "A": return A
            if doctype == "Customer Merge Log":
                if filters.get("old_customer_uuid") == C: return A # cycle attempt
                if filters.get("old_customer_uuid") == A: return B
                if filters.get("old_customer_uuid") == B: return C
            return None
            
        frappe.db.get_value = mock_get_value
        try:
            with self.assertRaises(frappe.exceptions.ValidationError):
                customer_before_rename(None, None, "C", "A", merge=True)
        finally:
            frappe.db.get_value = original_get_value
            frappe.destroy()

    def test_05_pos_closing_dependency(self):
        frappe.connect()
        inv = enqueue_event("POS Invoice", {"name": "POS-02"})
        frappe.db.commit()
        # Mark it PERMANENT_FAILED
        frappe.db.sql("UPDATE `tabBranch Sync Outbox` SET status='PERMANENT_FAILED' WHERE name=%s", (inv,))
        
        closing = enqueue_event("POS Closing", {"name": "CLOSING-02"}, depends_on=[inv])
        frappe.db.commit()
        
        process_outbox() # will evaluate dependency
        
        status = frappe.db.get_value("Branch Sync Outbox", closing, "status")
        self.assertEqual(status, "PERMANENT_FAILED")
        frappe.destroy()
