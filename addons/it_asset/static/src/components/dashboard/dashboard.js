/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart } from "@odoo/owl";

export class ITAssetDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.stats = {
            total_assets: 0,
            available: 0,
            assigned: 0,
            repair: 0,
            tickets_open: 0,
            account_requests_pending: 0,
            recent_activities: []
        };

        onWillStart(async () => {
            const realStats = await this.orm.call("it_asset.asset", "get_dashboard_stats", []);
            this.stats = {
                ...realStats,
                // Dummy data for future features
                tickets_open: 12,
                tickets_pending: 5,
                tickets_resolved: 45,
                account_requests_pending: 8,
                account_requests_approved: 24,
                kpi_performance: 94.5,
                kpi_trend: 1.2,
                recent_activities: [
                    { id: 1, type: 'ticket', title: 'Keyboard not working', user: 'Agus', time: '2 mins ago', status: 'new' },
                    { id: 2, type: 'request', title: 'New ERP Account', user: 'Siti', time: '15 mins ago', status: 'pending' },
                    { id: 3, type: 'asset', title: 'Macbook Air M2 Assigned', user: 'Budi', time: '1 hour ago', status: 'done' },
                ]
            };
        });
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
