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
            repair: 0,
            tickets_open: 12,
            account_requests_pending: 8,
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
            tickets_open: 12,
            account_requests_pending: 8,
            recent_activities: [
                { id: 1, type: 'ticket', title: 'Keyboard not working', user: 'Agus', time: '2 mins ago', status: 'new' },
                { id: 2, type: 'request', title: 'New ERP Account', user: 'Siti', time: '15 mins ago', status: 'pending' },
                { id: 3, type: 'asset', title: 'Macbook Air M2 Assigned', user: 'Budi', time: '1 hour ago', status: 'done' },
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
        if (state !== 'all') {
            domain = [['state', '=', state]];
            name = state.charAt(0).toUpperCase() + state.slice(1) + " Assets";
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
