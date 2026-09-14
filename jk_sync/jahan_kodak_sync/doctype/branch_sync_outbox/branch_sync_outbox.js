frappe.ui.form.on('Branch Sync Outbox', {
	refresh(frm) {
		if (frm.doc.status === 'PERMANENT_FAILED' || frm.doc.status === 'RETRYABLE_FAILED') {
			frm.add_custom_button(__('Retry Event'), function() {
				frappe.db.set_value('Branch Sync Outbox', frm.doc.name, 'status', 'PENDING')
				.then(r => {
					frappe.show_alert({message:__('Event queued for retry'), indicator:'green'});
					frm.reload_doc();
				});
			});
		}
	}
});
