# Dependency Graph

## Outbox Ordering

The `Branch Sync Outbox` uses a dynamic Directed Acyclic Graph (DAG) for dependency resolution, implemented via the `depends_on` column (JSON Array of event IDs).

### Business Rules
1. **POS Closing**:
   - `depends_on`: Every POS Invoice that was closed in that shift.
   - The Closing Entry will remain `PENDING` until all constituent invoices are `PROCESSED`.
   - If any invoice is `PERMANENT_FAILED`, the Closing Entry halts and becomes `PERMANENT_FAILED`, awaiting manual operator intervention.
2. **POS Exchange**:
   - Transmitted as a single composite business event payload.
   - Processed atomically in the Cloud receiver.

## Inbox Re-try Layer
If a POS Invoice is received by the Cloud but references missing master data (e.g., Customer, Item), it catches the `LinkValidationError` and marks the event as `RETRYABLE_FAILED`. The Branch will automatically retry it on the next poll until the master data converges.
