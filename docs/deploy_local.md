# Local Server (Branch) Deployment Guide

This document outlines the steps required to deploy the `jk_sync` Frappe application to your local physical branches (the Local Servers).

The Local Server runs isolated. It pushes POS transactions up to the Cloud and pulls down Master Data and Stock Deltas. Even if the internet drops for 24 hours, the local cashier terminals continue to function perfectly against this server.

## Prerequisites
- A production or local Frappe/ERPNext environment running physically inside the branch location.
- MariaDB and Redis must be running and healthy.

## 1. Get the Application Code
Navigate to your local frappe-bench directory and get the latest `jk_sync` code.

```bash
cd /path/to/frappe-bench

# If you haven't installed the app on the bench yet:
bench get-app jk_sync https://github.com/your-org/jk_sync.git

# If the app is already installed, pull the latest Revision 13 changes:
cd apps/jk_sync
git pull origin main
cd ../..
```

## 2. Install / Update the Application on the Local Site
Execute the database migrations to apply the critical local schema constraints (`Branch Sync Inbox`, `Local Stock Sync Log`).

```bash
# Ensure you specify the local branch site name
export SITE_NAME="branch01.local"

# Install the app to the site (if this is a completely new installation)
bench --site $SITE_NAME install-app jk_sync

# Apply latest database schema migrations
bench --site $SITE_NAME migrate
```

> [!CAUTION]
> The `bench migrate` step is absolutely mandatory. Revision 13 relies heavily on MariaDB database-level constraints. Without these constraints, the local server is vulnerable to duplicate stock processing if the internet connection is unstable.

## 3. Restart Local Services
Restart the local background workers to load the updated `master_data.py` and `outbox.py` worker logic.

```bash
# Restart Supervisor (or systemctl depending on your setup)
sudo supervisorctl restart all
```

## 4. Post-Deployment Configuration
Log in to the Local Server's Desk UI as an Administrator and execute these steps exactly:

1. **Configure Branch Config**:
   - Open `Branch Sync Config` (or your equivalent settings DocType).
   - Set the `Branch ID` (e.g., `BR01`). This MUST perfectly match the configuration on the Cloud Server.
   - Set the `Cloud URL` (e.g., `https://cloud.jahankodak.com`).
   - Provide the same `API Secret` shared with the Cloud Server for HMAC cryptographic validation.

2. **Verify POS Profiles**:
   - Ensure the Local Server POS Profiles are correctly assigned to the exact same Warehouse mapped to this branch on the Cloud server.

3. **Verify Background Jobs**:
   - Verify that the local background scheduler is actively running. The local server relies on Frappe's background workers to constantly poll the Outbox queue and sync with the Cloud.

## 5. Testing the Integration
1. Perform a test POS Invoice on the local terminal.
2. Check the `Branch Sync Outbox` on the Local Server. The event should appear as `PROCESSING` and then `PROCESSED`.
3. Check the Cloud Server to verify the Invoice was safely delivered.

## 6. Ongoing Maintenance
> [!WARNING]
> Just like the Cloud, the Local Server will accumulate massive logs. Ensure a scheduled job purges records from `Branch Sync Inbox`, `Branch Sync Outbox`, and `Local Stock Sync Log` where `status = 'PROCESSED'` after 90 days.
