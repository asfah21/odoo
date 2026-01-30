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
            printerPeriod: '7D',
            radioMode: 'digital',
            stats: {
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
                printer_stats: { total_color: 0, total_bw: 0, total_pages: 0, recent_pages: 0 }
            }
        });

        this.categories = [];
        this.selectedCategories = [];
        this.dateStart = null;
        this.dateEnd = null;
        this.fleetCategories = [];
        this.assetCategories = [];
        this.selectedFleetCategories = [];
        this.selectedAssetCategories = [];

        onWillStart(async () => {
            const categories = await this.orm.searchRead("it_asset.category", [], ["name", "is_consumable"]);
            this.categories = categories;
            this.assetCategories = categories.filter(c => !c.is_consumable && c.name.toLowerCase().includes('radio'));
            this.fleetCategories = await this.orm.searchRead("it_asset.unit.category", [], ["name"]);
            await this.loadDashboardData();
        });
    }

    async loadDashboardData() {
        const kwargs = {
            printer_period: this.state.printerPeriod,
            radio_mode: this.state.radioMode,
        };

        if (this.selectedCategories.length > 0) kwargs.category_ids = this.selectedCategories;
        if (this.selectedAssetCategories.length > 0) kwargs.comp_asset_cat_ids = this.selectedAssetCategories;
        if (this.selectedFleetCategories.length > 0) kwargs.fleet_category_ids = this.selectedFleetCategories;
        if (this.dateStart) kwargs.date_start = this.dateStart;
        if (this.dateEnd) kwargs.date_end = this.dateEnd;

        const res = await this.orm.call("it_asset.asset", "get_dashboard_stats", [], kwargs);

        // Update reactive state
        Object.assign(this.state.stats, res, {
            recent_activities: [
                { id: 1, type: 'asset', title: 'Asset audit completed', user: 'Admin', time: 'Just now', status: 'done' },
                { id: 2, type: 'asset', title: 'New laptop registered', user: 'Admin', time: '1 hour ago', status: 'new' },
            ]
        });
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
    }

    async onDateChange(type, ev) {
        if (type === 'start') this.dateStart = ev.target.value || null;
        if (type === 'end') this.dateEnd = ev.target.value || null;
        await this.loadDashboardData();
    }

    async setPrinterPeriod(period) {
        this.state.printerPeriod = period;
        await this.loadDashboardData();
    }

    get radioModeLabel() {
        const mode = this.state.radioMode || 'digital';
        return mode.charAt(0).toUpperCase() + mode.slice(1);
    }

    async setRadioMode(mode) {
        this.state.radioMode = mode;
        await this.loadDashboardData();
    }

    openView(state, assetType = 'it') {
        let domain = [['asset_type', '=', assetType]];
        let typeLabel = assetType === 'it' ? 'IT' : 'Operation';
        let name = `All ${typeLabel} Assets`;

        if (this.selectedCategories.length > 0 && assetType === 'it') {
            domain.push(['category_id', 'in', this.selectedCategories]);
        }

        if (state === 'unavailable') {
            domain.push('|', ['condition', '=', 'broken'], ['state', '=', 'retired']);
            name = `Unavailable ${typeLabel} Assets (Broken/Retired)`;
        } else if (state === 'maintenance_logs') {
            this.action.doAction({
                type: 'ir.actions.act_window',
                name: `${typeLabel} Management: Maintenance History`,
                res_model: 'it_asset.maintenance',
                views: [[false, 'list'], [false, 'form']],
                domain: [['asset_id.asset_type', '=', assetType]],
                target: 'current',
            });
            return;
        } else if (state !== 'all') {
            const domainState = state === 'assigned' ? 'in_use' : state;
            domain.push(['state', '=', domainState]);
            const formattedState = state.split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
            name = `${formattedState} ${typeLabel} Assets`;
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
