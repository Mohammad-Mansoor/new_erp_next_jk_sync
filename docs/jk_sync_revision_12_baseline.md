# jk_sync Revision 12 Baseline

## 1. Source Control
**Commit Hash**: 09bbbae (Baseline Revision 11)

## 2. Environment Info
- **ERPNext Version**: 15.110.0
- **Frappe Version**: 15.110.0
- **Python Version**: 3.12.3
- **MariaDB Version**: 10.6.27-MariaDB

## 3. Test Suite Results
Command: `bench run-tests --app jk_sync`

**Result**: FAILED (Exit Code 1)

**Root Cause of Failure**:
The `after_insert` hook on `Stock Ledger Entry` (`jk_sync.sync.stock.log_stock_ledger_entry`) crashes because it queries the `branch_id` field on the `Warehouse` DocType, but that Custom Field was never created in the database schema.

```python
pymysql.err.OperationalError: (1054, "Unknown column 'branch_id' in 'SELECT'")
```
