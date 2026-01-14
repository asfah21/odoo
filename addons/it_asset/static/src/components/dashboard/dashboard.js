/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart } from "@odoo/owl";

export class ITAssetDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.categories = [];
        this.selectedCategory = null;
        this.dateStart = null;
        this.dateEnd = null;

        this.stats = {
            total_assets: 0,
            available: 0,
            assigned: 0,
            unavailable_broken: 0,
            maintenance_count: 0,
            recent_activities: [],
            state_distribution: [],
            category_distribution: []
        };

        onWillStart(async () => {
            await this.loadDashboardData();
            this.categories = await this.orm.searchRead("it_asset.category", [], ["name"]);
        });
    }

    async loadDashboardData() {
        const params = {};
        if (this.selectedCategory) {
            params.category_id = this.selectedCategory;
        }
        if (this.dateStart) params.date_start = this.dateStart;
        if (this.dateEnd) params.date_end = this.dateEnd;

        const res = await this.orm.call("it_asset.asset", "get_dashboard_stats", [], params);

        this.stats = {
            ...this.stats,
            ...res,
            recent_activities: [
                { id: 1, type: 'asset', title: 'Asset audit completed', user: 'Admin', time: 'Just now', status: 'done' },
                { id: 2, type: 'asset', title: 'New laptop registered', user: 'Admin', time: '1 hour ago', status: 'new' },
            ]
        };
    }

    async onFilterCategory(ev) {
        this.selectedCategory = ev.target.value ? parseInt(ev.target.value) : null;
        await this.loadDashboardData();
        this.render();
    }

    async onDateChange(type, ev) {
        if (type === 'start') this.dateStart = ev.target.value || null;
        if (type === 'end') this.dateEnd = ev.target.value || null;
        await this.loadDashboardData();
        this.render();
    }

    openView(state) {
        let domain = [];
        let name = "All Assets";

        if (state === 'unavailable') {
            domain = [['condition', '=', 'broken']];
            name = "Broken Assets (Unavailable)";
        } else if (state === 'maintenance_logs') {
            this.action.doAction({
                type: 'ir.actions.act_window',
                name: "Maintenance History",
                res_model: 'it_asset.maintenance',
                views: [[false, 'list'], [false, 'form']],
                target: 'current',
            });
            return;
        } else if (state !== 'all') {
            domain = [['state', '=', state]];
            const formattedState = state.split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
            name = formattedState + " Assets";
        }

        this.action.doAction({
            type: 'ir.actions.act_window',
            name: name,
            res_model: 'it_asset.asset',
            views: [[false, 'list'], [false, 'form']],
            domain: domain,
            target: 'current',
        });
    }
}

ITAssetDashboard.template = "it_asset.DashboardMain";
registry.category("actions").add("it_asset_dashboard_action", ITAssetDashboard);
