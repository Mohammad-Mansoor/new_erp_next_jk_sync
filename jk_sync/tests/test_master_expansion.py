import frappe
from jk_sync.api.master import get_master_updates
from jk_sync.sync.master_data import process_master_updates
import json

def run():
    frappe.flags.in_test = True
    
    # 1. Test get_master_updates directly
    frappe.request = type('Request', (), {
        'headers': {'X-Branch-ID': 'TEST-BR'},
        'get_data': lambda: json.dumps({'last_master_data_sync': '2026-01-01 00:00:00'})
    })
    
    res = get_master_updates()
    print("Cloud generated payload length for Master Data:", len(res.get('master_data', {}).keys()))
    
    if res.get('status') == 'SUCCESS':
        print("Success generated master updates!")
    else:
        print("Failed:", res.get('message'))
        
    print("Testing processing payload...")
    # 2. Test process_master_updates
    # We will pass empty data or dummy data just to see it doesn't crash
    try:
        process_master_updates(res)
        print("process_master_updates executed without crashing.")
    except Exception as e:
        print("Error processing updates:", e)
