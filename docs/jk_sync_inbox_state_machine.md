# Inbox State Machine & Transaction Boundaries

## Overview
The Branch Sync Inbox acts as the idempotent receiver for the system. It enforces a strict exactly-once processing guarantee using MariaDB's InnoDB locking semantics.

## Transaction Boundaries
1. **Request Received**: HTTP POST `receive_sync_event()`.
2. **Atomic Insert Lock**: 
   - `INSERT INTO tabBranch Sync Inbox (event_id, status) VALUES (X, 'PROCESSING')`
   - **Isolation Behavior**: InnoDB acquires a row-level exclusive lock on the primary key (`name` = `event_id`).
   - If the insert succeeds, the current worker definitively owns the event.
   - `frappe.db.commit()` immediately commits this lock so it is visible globally.
3. **Duplicate Collision**:
   - If `DuplicateEntryError` occurs, another request has already inserted `event_id`.
   - The worker executes `SELECT ... FOR UPDATE` to read the existing state, which forces it to wait if the other worker is currently writing to it.
4. **Processing & Completion**:
   - The winning worker executes the POS business handler within a new transaction savepoint.
   - Upon success, it updates `status = 'PROCESSED'` and commits.
   - If it fails, it rolls back the savepoint and updates `status = 'FAILED'`.
