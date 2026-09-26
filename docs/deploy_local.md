# Local Server (Branch) Deployment & Configuration Guide (`jk_sync`)

This document provides complete, step-by-step instructions for deploying and configuring the `jk_sync` application on physical **Local Branch Servers**.

The Local Branch Server runs inside the physical store. Cashiers execute POS Sales, Exchanges, and Cashier Closings against this server. Even if the internet connection drops for 24+ hours, local terminals operate at 100% speed. Once connectivity restores, outbox workers push transactions to the Cloud and pull down updates automatically.

---

## 1. Prerequisites & Installation

### Step 1.1: Install App on Local Bench & Apply Migrations
On the local branch server/PC, open terminal inside your `frappe-bench` directory:

```bash
cd /path/to/frappe-bench

# Get app
bench get-app jk_sync https://github.com/your-org/jk_sync.git

# Install app to local branch site
export SITE_NAME="branch01.local"
bench --site $SITE_NAME install-app jk_sync

# Execute database migrations
bench --site $SITE_NAME migrate
```

> [!NOTE]
> Running `bench migrate` executes the `after_migrate` hook in `install.py`, which **automatically enables the background scheduler** (`enable_scheduler()`) on the site.

### Step 1.2: Verify Production Services
Ensure local background worker services are running continuously:

```bash
sudo supervisorctl restart all
```

---

## 2. Step-by-Step Local Configuration

### Step 2.1: Configure `Branch Sync Config` (Single DocType)
Log in to the local branch server Desk UI as **System Manager** / **Administrator**.

1. Go to **Awesomebar / Search** -> Type **Branch Sync Config** -> Press Enter.
2. Fill in the following exact fields:

| Field Name | Field Type | Example Value | Description |
| :--- | :--- | :--- | :--- |
| **Branch ID** (`branch_id`) | Data | `BR01` | **MUST** match the `branch_id` created in `Cloud Branch Master` on the Cloud server. |
| **Cloud URL** (`cloud_url`) | Data | `https://cloud.jahankodak.com` | Full HTTPS domain of the Cloud server (no trailing slash). |
| **API Key** (`api_key`) | Data | `br01_key_89234` | Matching `api_key` configured on Cloud. |
| **API Secret** (`api_secret`) | Password | `br01_sec_9948172` | Matching `api_secret` configured on Cloud. |
| **Last Master Data Sync** (`last_master_data_sync`) | Datetime | *(Leave Blank)* | Leave blank for initial deployment to trigger full master pull. |

3. Click **Save**.

---

### Step 2.2: Initial Master Data Synchronization (First Pull)
Once `Branch Sync Config` is saved, the local background worker `poll_master_data` will poll the Cloud every 1 minute.

To trigger the first master data pull immediately:

```bash
bench --site $SITE_NAME execute jk_sync.sync.master_data.poll_master_data
```

This automatically downloads all Items, Customers, POS Profiles, Warehouses, Users, Item Prices, Taxes, and System Settings from the Cloud to the local database.

---

### Step 2.3: Initial Opening Stock Sync (1-Click UI Button)
To pull available stock balances for your warehouse from the Cloud without double-counting old receipts:

1. Open `Branch Sync Config` form in the local Desk UI.
2. Click the blue button at the top right: **`Sync Opening Stock from Cloud`**.
3. A confirmation dialog will pop up: *"Are you sure you want to pull opening stock balances from Cloud?"* -> Click **Yes**.
4. The local server sends an HMAC-signed request to the Cloud, reads current available quantities from `tabBin`, and generates local `Material Receipt` Stock Entries with `allow_zero_valuation_rate = 1`.
5. A green success message will pop up: **"Successfully created local Opening Stock Entries for X items."**

---

### Step 2.4: Verify Local POS Profile & Cashier Logins
1. Go to **POS Profile** and verify that the synced profile (e.g. `Kabul Cash Register`) is mapped to the local warehouse (e.g. `Stores - BR01`).
2. Log in as a local cashier `User` (assigned to `BR01`).
3. Open **POS Invoice** -> Create a test invoice -> Click **Submit**.

---

## 3. Verifying Outbox Synchronization

To confirm that local POS sales are syncing up to the Cloud:

1. Go to **Awesomebar / Search** -> Type **Branch Sync Outbox**.
2. Locate the event for your test invoice (`event_type = 'POS Invoice'`).
3. Check the **Status** field:
   * **`PENDING`**: Waiting for next 1-minute cron cycle.
   * **`PROCESSING`**: Currently being transmitted over HTTP.
   * **`PROCESSED`**: Successfully delivered and submitted on Cloud!
4. Log in to the Cloud Server to verify that the POS Invoice was safely created with the exact same name (e.g., `BR01-POS-0001`).

---

## 4. Offline Resilience Testing

To test 24-hour offline resilience:

1. Disconnect the local branch server from the internet (unplug network cable or disable Wi-Fi).
2. Create and submit 5 POS Invoices on the local terminal. Notice that transactions complete instantly with **zero delay**.
3. Check `Branch Sync Outbox` -> Events remain in `RETRYABLE_FAILED` or `PENDING` state while offline.
4. Reconnect the network cable.
5. Within 1 minute, the local worker automatically detects network restoration, pushes all 5 pending invoices to the Cloud, and marks their status as `PROCESSED`.

---

## 5. Verification Checklist for Local Server

- [x] App `jk_sync` installed and `bench migrate` executed.
- [x] `Branch Sync Config` saved with correct `branch_id`, `cloud_url`, `api_key`, and `api_secret`.
- [x] Initial Master Data pulled via `poll_master_data`.
- [x] Opening stock populated using **`Sync Opening Stock from Cloud`** button.
- [x] Local POS Profile verified with cashier login.
- [x] Test POS Invoice successfully pushed to Cloud (`status = 'PROCESSED'`).
- [x] Background scheduler verified active.
