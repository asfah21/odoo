/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart } from "@odoo/owl";

export class ITAssetDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.stats = {};

        onWillStart(async () => {
            this.stats = await this.orm.call("it_asset.asset", "get_dashboard_stats", []);
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
