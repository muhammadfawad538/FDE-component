frappe.listview_settings['SyncSchedule'] = {
	get_indicator: function (doc) {
		return {
			status: doc.active ? 'Active' : 'Inactive',
			color: doc.active ? 'green' : 'gray'
		};
	}
};
