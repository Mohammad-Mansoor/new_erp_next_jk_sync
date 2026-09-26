# Cloud Server Branch Configuration Guide (`jk_sync`)

This document provides complete, step-by-step instructions for deploying and configuring the `jk_sync` application on your centralized **Cloud Server**. 

The Cloud Server acts as the master node. It receives POS Closing Entries, POS Invoices, and POS Exchanges from physical branch servers, and it distributes Master Data updates and Stock Ledger Deltas to all branch servers.

---

## 1. Prerequisites & Installation

### Step 1.1: Install App on Bench & Apply Migrations
Navigate to your `frappe-bench` directory on the Cloud server:

```bash
cd /path/to/frappe-bench

# Get app (if not already downloaded)
bench get-app jk_sync https://github.com/your-org/jk_sync.git

# Install app to your Cloud site
export SITE_NAME="cloud.jahankodak.com"
bench --site $SITE_NAME install-app jk_sync

# Execute database migrations (CRITICAL: applies database unique constraints & custom fields)
bench --site $SITE_NAME migrate
```

### Step 1.2: Verify Background Scheduler
Ensure the background job scheduler is active on the Cloud site:

```bash
bench --site $SITE_NAME enable-scheduler
```

### Step 1.3: Restart Production Services
Restart background workers and web services to load Python backend code into memory:

```bash
sudo supervisorctl restart all
# or: sudo systemctl restart frappe-bench-web.service frappe-bench-workers.service
```

---

## 2. Step-by-Step Cloud Configuration

### Step 2.1: Register Branches in DocType `Cloud Branch Master`
For every physical retail branch in your enterprise, you must create a record in `Cloud Branch Master`.

1. Log in to Desk UI as **System Manager** / **Administrator**.
2. Go to **Awesomebar / Search** -> Type **Cloud Branch Master** -> Click **New**.
3. Fill in the following exact fields:

| Field Name | Field Type | Example Value | Description |
| :--- | :--- | :--- | :--- |
| **Branch ID** (`branch_id`) | Data (Unique) | `BR01` | Unique string identifier for the branch location. |
| **API Key** (`api_key`) | Data | `br01_key_89234` | Unique key string shared with the branch server. |
| **API Secret** (`api_secret`) | Password | `br01_sec_9948172` | Secret key used for HMAC SHA256 request signing. |

4. Click **Save**. Repeat for all branches (`BR01`, `BR02`, `BR03`, etc.).

---

### Step 2.2: Map Branch ID to Warehouses in DocType `Warehouse`
To ensure stock changes made on the Cloud (such as Purchase Receipts or Stock Entries) automatically replicate to the branch server, you must link the branch ID to the branch warehouse.

1. Go to **Stock** -> **Warehouse**.
2. Open the warehouse belonging to the branch (e.g., `Stores - BR01`).
3. Locate the Custom Field **Branch ID** (`branch_id`).
4. Select the corresponding `Cloud Branch Master` link (e.g., `BR01`).
5. Click **Save**.

> [!IMPORTANT]
> Whenever a `Stock Ledger Entry` is created on the Cloud for a Warehouse with `branch_id = 'BR01'`, the `log_stock_ledger_entry` hook will automatically record a delta entry in `Cloud Stock Sync Log` for branch `BR01`.

---

### Step 2.3: Assign Branch ID to Users in DocType `User`
To enforce user authorization during POS transaction sync, cashiers and store managers must be assigned to their branch.

1. Go to **Users and Permissions** -> **User**.
2. Open the user account for a branch cashier (e.g., `cashier.kabul@jahankodak.com`).
3. Locate the Custom Field **Branch ID** (`branch_id`).
4. Select the branch ID (e.g., `BR01`).
5. Click **Save**.

> [!NOTE]
> `Administrator` is exempt from branch authorization checks. For all other users, `api/receiver.py` verifies that `User.branch_id == X-Branch-ID` on incoming POS Invoices.

---

### Step 2.4: Setup Master Data & Pricing
Configure all standard ERPNext master records on the Cloud server. The branch servers will pull these automatically:

*   **Items & Item Groups** (`Item`, `Item Group`, `UOM`)
*   **Item Prices & Pricing Rules** (`Item Price`, `Pricing Rule`)
*   **Customers & Customer Groups** (`Customer`, `Customer Group`, `Customer Sync ID`)
*   **POS Profiles** (`POS Profile`, `POS Payment Method`, `Mode of Payment`)
*   **Taxes & Accounts** (`Item Tax Template`, `Sales Taxes and Charges Template`, `Account`)

---

## 3. Cloud Endpoints & Network Requirements

Ensure your Cloud firewall and web server (Nginx) allow external HTTPS POST requests from branch servers to these whitelisted endpoints:

1. **`receive_sync_event`**: `https://<cloud-domain>/api/method/jk_sync.api.receiver.receive_sync_event`
   * *Receives POS Invoices, POS Exchanges, POS Openings, and POS Closings from branches.*
2. **`get_master_updates`**: `https://<cloud-domain>/api/method/jk_sync.api.master.get_master_updates`
   * *Used by branch servers to poll for master data updates and stock deltas.*
3. **`get_opening_stock_snapshot`**: `https://<cloud-domain>/api/method/jk_sync.api.master.get_opening_stock_snapshot`
   * *Used by branch servers during 1-click initial stock initialization.*
4. **`ack_stock_delta`**: `https://<cloud-domain>/api/method/jk_sync.api.master.ack_stock_delta`
   * *Receives acknowledgements from branches after stock deltas are applied locally.*

---

## 4. Verification Checklist for Cloud

- [x] App `jk_sync` installed and `bench migrate` executed cleanly.
- [x] DocType `Cloud Branch Master` has records for all physical branches.
- [x] Custom Field `branch_id` is set on every branch `Warehouse` record.
- [x] Custom Field `branch_id` is set on every branch `User` record.
- [x] Background scheduler is active (`bench enable-scheduler`).
- [x] HTTPS endpoints accessible over public internet.
