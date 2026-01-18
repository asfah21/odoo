/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

export class ITAssetDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            showMobileFilters: false,
        });
        this.categories = [];
        this.selectedCategories = [];
        this.dateStart = null;
        this.dateEnd = null;
        this.fleetCategories = [];
        this.assetCategories = [];
        this.selectedFleetCategories = [];
        this.selectedAssetCategories = [];

        this.stats = {
            total_assets: 0,
            total_it: 0,
            total_operation: 0,
            available: 0,
            assigned: 0,
            unavailable_broken: 0,
            op_available: 0,
            op_assigned: 0,
            op_unavailable_broken: 0,
            op_maintenance: 0,
            maintenance_count: 0,
            recent_activities: [],
            state_distribution: [],
            category_distribution: [],
            fleet_comparison: { assets: 0, units: 0, ratio: 0 },
            printer_stats: { total_color: 0, total_bw: 0, total_pages: 0, recent_pages: 0, top_printer: 'N/A' }
        };

        onWillStart(async () => {
            this.categories = await this.orm.searchRead("it_asset.category", [], ["name", "is_consumable"]);
            // Only show "Radio" related categories in the Fleet vs Asset comparison
            this.assetCategories = this.categories.filter(c => !c.is_consumable && c.name.toLowerCase().includes('radio'));
            this.fleetCategories = await this.orm.searchRead("it_asset.unit.category", [], ["name"]);
            await this.loadDashboardData();
        });
    }

    async loadDashboardData() {
        const params = {};
        if (this.selectedCategories.length > 0) {
            params.category_ids = this.selectedCategories;
        }
        if (this.selectedAssetCategories.length > 0) {
            params.comp_asset_cat_ids = this.selectedAssetCategories;
        }
        if (this.selectedFleetCategories.length > 0) {
            params.fleet_category_ids = this.selectedFleetCategories;
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

    get selectedCategoriesNames() {
        if (this.selectedCategories.length === 0) return "All Categories";
        if (this.selectedCategories.length === 1) {
            const cat = this.categories.find(c => c.id === this.selectedCategories[0]);
            return cat ? cat.name : "All Categories";
        }
        return `${this.selectedCategories.length} Categories`;
    }

    async toggleCategory(categoryId) {
        if (categoryId === null) {
            this.selectedCategories = [];
        } else {
            const index = this.selectedCategories.indexOf(categoryId);
            if (index > -1) {
                this.selectedCategories.splice(index, 1);
            } else {
                this.selectedCategories.push(categoryId);
            }
        }
        await this.loadDashboardData();
        this.render();
    }

    async toggleAssetCategory(catId) {
        if (catId === null) {
            this.selectedAssetCategories = [];
        } else {
            const idx = this.selectedAssetCategories.indexOf(catId);
            if (idx > -1) this.selectedAssetCategories.splice(idx, 1);
            else this.selectedAssetCategories.push(catId);
        }
        await this.loadDashboardData();
        this.render();
    }

    async toggleFleetCategory(catId) {
        if (catId === null) {
            this.selectedFleetCategories = [];
        } else {
            const idx = this.selectedFleetCategories.indexOf(catId);
            if (idx > -1) this.selectedFleetCategories.splice(idx, 1);
            else this.selectedFleetCategories.push(catId);
        }
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
        let domain = [['asset_type', '=', 'it']];
        let name = "All IT Assets";

        if (this.selectedCategories.length > 0) {
            domain.push(['category_id', 'in', this.selectedCategories]);
        }

        if (state === 'unavailable') {
            domain.push(['condition', '=', 'broken']);
            name = "Broken IT Assets (Unavailable)";
        } else if (state === 'maintenance_logs') {
            this.action.doAction({
                type: 'ir.actions.act_window',
                name: "IT Management: Maintenance History",
                res_model: 'it_asset.maintenance',
                views: [[false, 'list'], [false, 'form']],
                domain: [['asset_id.asset_type', '=', 'it']],
                target: 'current',
            });
            return;
        } else if (state !== 'all') {
            domain.push(['state', '=', state]);
            const formattedState = state.split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
            name = formattedState + " IT Assets";
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
