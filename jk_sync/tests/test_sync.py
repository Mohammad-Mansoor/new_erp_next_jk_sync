import frappe
import unittest
import uuid
import time
import json
import threading
from jk_sync.sync.outbox import process_outbox, enqueue_event
from jk_sync.sync.master_data import customer_before_rename

class TestJKSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.flags.in_test = True
        
        # Setup masters for tests
        if not frappe.db.exists("Item Group", "Test Group"):
            frappe.get_doc({"doctype": "Item Group", "item_group_name": "Test Group", "parent_item_group": "All Item Groups"}).insert(ignore_permissions=True)
            
        if not frappe.db.exists("Item", "Test Camera"):
            frappe.get_doc({
                "doctype": "Item",
                "item_code": "Test Camera",
                "item_name": "Test Camera",
                "item_group": "Test Group",
                "is_stock_item": 1
            }).insert(ignore_permissions=True)
            
        try:
            if not frappe.db.exists("Warehouse", "Stores - TC"):
                frappe.get_doc({
                    "doctype": "Warehouse",
                    "warehouse_name": "Stores",
                    "company": frappe.db.get_single_value('Global Defaults', 'default_company') or frappe.get_all("Company")[0].name
                }).insert(ignore_permissions=True)
        except frappe.exceptions.DuplicateEntryError:
            pass
        
    def test_inbox_concurrency(self):
        """
        Simulate 3 concurrent requests hitting the Inbox Receiver for the same event_id.
        """
        event_id = str(uuid.uuid4())
        payload_hash = "H1"
        branch_id = "BR01"
        
        # Worker 1
        claim_token_1 = str(uuid.uuid4())
        doc = frappe.get_doc({
            "doctype": "Branch Sync Inbox",
            "event_id": event_id,
            "payload_hash": payload_hash,
            "branch_id": branch_id,
            "status": "PROCESSING",
            "claim_token": claim_token_1
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Worker 2 attempts same insert (Concurrent)
        claim_token_2 = str(uuid.uuid4())
        with self.assertRaises(frappe.exceptions.DuplicateEntryError):
            doc2 = frappe.get_doc({
                "doctype": "Branch Sync Inbox",
                "event_id": event_id,
                "payload_hash": payload_hash,
                "branch_id": branch_id,
                "status": "PROCESSING",
                "claim_token": claim_token_2
            })
            doc2.insert(ignore_permissions=True)
            
    def test_stock_delta_idempotency_lost_ack(self):
        """
        Simulate a stock delta arriving, being processed, and then arriving again (Lost ACK).
        """
        stock_delta_id = str(uuid.uuid4())
        
        # First arrival
        doc = frappe.get_doc({
            "doctype": "Local Stock Sync Log",
            "stock_delta_id": stock_delta_id,
            "item_code": "Test Camera",
            "warehouse": "Stores - TC",
            "qty_change": 50.0,
            "status": "PROCESSED"
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Second arrival (Lost ACK retry from Cloud)
        with self.assertRaises(frappe.exceptions.DuplicateEntryError):
            doc2 = frappe.get_doc({
                "doctype": "Local Stock Sync Log",
                "stock_delta_id": stock_delta_id,
                "item_code": "Test Camera",
                "warehouse": "Stores - TC",
                "qty_change": 50.0,
                "status": "PROCESSING"
            })
            doc2.insert(ignore_permissions=True)
            
    def test_customer_merge_cycles(self):
        """
        Proves deep loop detection A -> B, B -> C, C -> A.
        """
        uid = str(uuid.uuid4())[:8]
        A = f"A_{uid}"
        B = f"B_{uid}"
        C = f"C_{uid}"
        
        frappe.get_doc({
            "doctype": "Customer Merge Log",
            "old_customer_uuid": A,
            "new_customer_uuid": B
        }).insert(ignore_permissions=True)
        
        frappe.get_doc({
            "doctype": "Customer Merge Log",
            "old_customer_uuid": B,
            "new_customer_uuid": C
        }).insert(ignore_permissions=True)
        
        # Mock frappe.db.get_value to simulate C -> A cycle check
        original_get_value = frappe.db.get_value
        def mock_get_value(doctype, filters, fieldname=None):
            if doctype == "Customer":
                if filters == "C": return C
                if filters == "A": return A
            if doctype == "Customer Merge Log":
                if filters.get("old_customer_uuid") == C: return A # C -> A
                if filters.get("old_customer_uuid") == A: return B # A -> B
                if filters.get("old_customer_uuid") == B: return C # B -> C
            return None
            
        frappe.db.get_value = mock_get_value
        
        try:
            with self.assertRaises(frappe.exceptions.ValidationError):
                customer_before_rename(None, None, "C", "A", merge=True)
        finally:
            frappe.db.get_value = original_get_value

    def test_pos_closing_dependencies(self):
        """
        Proves POS Closing Entry waits for POS Invoices to hit PROCESSED.
        """
        inv_event_id = enqueue_event("POS Invoice", {"name": "POS-01"})
        closing_event_id = enqueue_event("POS Closing", {"name": "CLOSING-01"}, depends_on=[inv_event_id])
        process_outbox() 
