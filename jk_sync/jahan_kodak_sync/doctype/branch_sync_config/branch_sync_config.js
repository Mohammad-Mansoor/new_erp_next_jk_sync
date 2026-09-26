// Copyright (c) 2026, Jahan Kodak and contributors
// For license information, please see license.txt

frappe.ui.form.on("Branch Sync Config", {
	refresh(frm) {
		if (frm.doc.cloud_url && frm.doc.branch_id) {
			frm.add_custom_button(__("Sync Opening Stock from Cloud"), function() {
				frappe.confirm(
					__("Are you sure you want to pull opening stock balances from Cloud? This will create a local Stock Entry for available stock."),
					function() {
						frappe.call({
							method: "jk_sync.sync.master_data.sync_opening_stock_from_cloud",
							freeze: true,
							freeze_message: __("Fetching opening stock snapshot from Cloud..."),
							callback: function(r) {
								if (r.message && r.message.status === "SUCCESS") {
									frappe.msgprint({
										title: __("Stock Sync Completed"),
										indicator: "green",
										message: r.message.message
									});
								} else if (r.message && r.message.message) {
									frappe.msgprint({
										title: __("Sync Error"),
										indicator: "red",
										message: r.message.message
									});
								}
							}
						});
					}
				);
			}).addClass("btn-primary");
		}
	},
});

