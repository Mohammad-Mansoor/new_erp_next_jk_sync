# Cloud Server Deployment Guide

This document outlines the steps required to deploy the `jk_sync` Frappe application to your centralized **Cloud Server**. 

The Cloud Server acts as the master node. It receives POS Closing Entries and Invoices from the branches, and it generates Stock Deltas and Master Data updates that branches pull down.

## Prerequisites
- A production Frappe/ERPNext environment running on your cloud instance.
- Ensure MariaDB and Redis are running and healthy.

## 1. Get the Application Code
Navigate to your frappe-bench directory and get the latest `jk_sync` code.

```bash
cd /path/to/frappe-bench

# If you haven't installed the app on the bench yet:
bench get-app jk_sync https://github.com/your-org/jk_sync.git

# If the app is already installed, pull the latest Revision 13 changes:
cd apps/jk_sync
git pull origin main
cd ../..
```

## 2. Install / Update the Application on the Cloud Site
You must execute the database migrations to apply the new schema constraints and DocTypes.

```bash
# Ensure you specify your cloud site name
export SITE_NAME="cloud.jahankodak.com"

# Install the app to the site (if this is a completely new installation)
bench --site $SITE_NAME install-app jk_sync

# Apply latest database schema migrations (CRITICAL for Revision 13)
bench --site $SITE_NAME migrate
```

> [!CAUTION]
> The `bench migrate` step is absolutely mandatory. Revision 13 relies heavily on MariaDB database-level constraints (like `UNIQUE(stock_delta_id)`). If migrations are not run, the exactly-once processing guarantees will fail.

## 3. Restart Production Services
Since we have updated backend Python logic (`api/receiver.py`), the Frappe python workers and background job schedulers must be restarted to load the new code into memory.

```bash
# Restart Supervisor (or systemctl depending on your setup)
sudo supervisorctl restart all

# Alternatively, if using systemd:
# sudo systemctl restart frappe-bench-web.service
# sudo systemctl restart frappe-bench-workers.service
```

## 4. Post-Deployment Verification
Log in to the Cloud Server's Desk UI as an Administrator and verify:

1. **DocTypes Exist**: Check that `Cloud Stock Sync Log` and `Branch Sync Config` exist.
2. **Branch Config**: Open `Branch Sync Config` and ensure that every physical branch (e.g., `BR01`, `BR02`) has a configured entry with an API secret.
3. **API Access**: Ensure that the Cloud Server's API endpoints are reachable over the internet (HTTPS) so branches can securely POST to `/api/method/jk_sync.api.receiver.receive_sync_event`.

## 5. Ongoing Maintenance
> [!WARNING]
> Over years of operation, synchronization logs will consume significant database storage. Ensure you configure a Frappe scheduled job (or cron) to periodically delete records from `Cloud Stock Sync Log` where `status = 'PROCESSED'` and the creation date is older than 90 days.
