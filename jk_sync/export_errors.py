import frappe
import csv
import os

def export_to_csv():
    # Fetch all Error Logs, ordered by creation (newest first)
    logs = frappe.get_all("Error Log", fields=["name", "creation", "method", "error"], order_by="creation desc", limit_page_length=500)
    
    file_path = os.path.join(frappe.utils.get_bench_path(), "error_logs_export.csv")
    
    with open(file_path, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(["ID", "Date", "Method", "Error Traceback"])
        
        for log in logs:
            writer.writerow([log.name, log.creation, log.method, log.error])
            
    print(f"Successfully exported {len(logs)} error logs to {file_path}")
