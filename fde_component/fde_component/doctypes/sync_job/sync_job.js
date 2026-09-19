/**
 * SyncJob Detail View
 * -------------------
 * Adds:
 *   - Progress bar in the header
 *   - Checkpoint timeline visualization
 *   - DLQ tab with Re-drive action
 *   - Resume button for Interrupted jobs
 */

frappe.ui.form.on('SyncJob', {
	refresh: function (frm) {
		const doc = frm.doc;
		const status = doc.status;

		// Progress bar
		const processed = doc.processed || 0;
		const total = doc.total_records || 0;
		const pct = total > 0 ? Math.round((processed / total) * 100) : 0;

		frm.dashboard.add_progress(__('Progress'), pct, {
			color: pct === 100 ? 'green' : 'blue'
		});

		// Resume button
		if (status === 'Interrupted') {
			frm.add_custom_button(__('Resume'), function () {
				frm.call('resume_job').then(() => {
					frappe.msgprint(__('Job resumed'));
					frm.reload_doc();
				});
			}).removeClass('btn-default').addClass('btn-primary');
		}

		// DLQ tab
		if (status === 'Dead Lettered') {
			frm.add_custom_button(__('Re-drive from DLQ'), function () {
				frappe.call({
					method: 'fde_component.api.redrive_from_dlq',
					args: { job_name: doc.name },
					callback: function (r) {
						if (r.message) {
							frappe.msgprint(__('Job re-drive initiated'));
							frm.reload_doc();
						}
					}
				});
			}).removeClass('btn-default').addClass('btn-warning');
		}

		// Listen for realtime progress updates
		frm.events.setup_realtime(frm);
	},

	setup_realtime: function (frm) {
		const job_name = frm.doc.name;
		frappe.realtime.on('job_progress', function (data) {
			if (data.job_name === job_name && frm.doc) {
				frm.doc.processed = data.processed;
				frm.doc.total_records = data.total;
				frm.refresh_field('processed');
				frm.refresh_field('total_records');
			}
		});
	}
});
