import frappe
from jk_sync.handlers.pos_opening import handle_pos_opening

def run():
    frappe.init(site="development.localhost")
    frappe.connect()

    payload = {
        "doc": {
            "__unsaved": 1,
            "amended_from": None,
            "balance_details": [
                {
                    "docstatus": 1,
                    "doctype": "POS Opening Entry Detail",
                    "idx": 1,
                    "mode_of_payment": "Cash",
                    "name": "tef3cqq34q_new",
                    "opening_amount": 0.0,
                    "owner": "Administrator",
                    "parent": "POS-OPE-2026-99999",
                    "parentfield": "balance_details",
                    "parenttype": "POS Opening Entry"
                }
            ],
            "company": "Jahan Kodak",
            "docstatus": 1,
            "doctype": "POS Opening Entry",
            "idx": 0,
            "name": "POS-OPE-2026-99999",
            "owner": "Administrator",
            "period_end_date": None,
            "period_start_date": "2026-09-24 19:47:15.140597",
            "pos_closing_entry": None,
            "pos_profile": "Kabul Cash Register",
            "posting_date": "2026-09-24",
            "set_posting_date": 0,
            "status": "Open",
            "user": "Administrator"
        }
    }
    
    try:
        handle_pos_opening(payload)
        print("Success")
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run()
