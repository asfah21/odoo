from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ITAssetMaterialRequestFulfillWizard(models.TransientModel):
    _name = 'it_asset.material_request.fulfill.wizard'
    _description = 'Fulfill Material Request Wizard'

    request_id = fields.Many2one(
        'it_asset.material_request',
        string='Material Request',
        required=True,
        readonly=True
    )
    line_ids = fields.One2many(
        'it_asset.material_request.fulfill.wizard.line',
        'wizard_id',
        string='Items to Fulfill'
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        request_id = self.env.context.get('default_request_id') or self.env.context.get('active_id')
        if request_id:
            request = self.env['it_asset.material_request'].browse(request_id)
            res['request_id'] = request.id
            wizard_lines = []
            for line in request.line_ids:
                remaining = max(0.0, line.quantity - line.qty_fulfilled)
                if remaining > 0:
                    wizard_lines.append((0, 0, {
                        'line_id': line.id,
                        'name': line.name,
                        'uom': line.uom,
                        'qty_requested': line.quantity,
                        'qty_previously_fulfilled': line.qty_fulfilled,
                        'qty_remaining': remaining,
                        'qty_to_fulfill': remaining,
                    }))
            res['line_ids'] = wizard_lines
        return res

    def action_confirm(self):
        self.ensure_one()
        request = self.request_id
        if not self.line_ids:
            raise UserError(_("No items to fulfill."))

        fulfilled_details = []
        has_any_fulfillment = False

        for wizard_line in self.line_ids:
            orig_line = wizard_line.line_id
            item_name = (orig_line and orig_line.name) or wizard_line.name or _("Item")
            actual_remaining = max(0.0, orig_line.quantity - orig_line.qty_fulfilled) if orig_line else wizard_line.qty_remaining

            if wizard_line.qty_to_fulfill < 0:
                raise UserError(_("Fulfillment quantity for '%s' cannot be negative.") % item_name)
            if wizard_line.qty_to_fulfill > actual_remaining:
                raise UserError(
                    _("Fulfillment quantity for '%s' (%.2f) exceeds remaining quantity (%.2f).")
                    % (item_name, wizard_line.qty_to_fulfill, actual_remaining)
                )
            if wizard_line.qty_to_fulfill > 0:
                has_any_fulfillment = True
                if orig_line:
                    orig_line.qty_fulfilled += wizard_line.qty_to_fulfill
                uom_name = (orig_line and orig_line.uom) or wizard_line.uom or ""
                uom_str = f" {uom_name}" if uom_name else ""
                fulfilled_details.append(
                    f"<li><b>{item_name}</b>: {wizard_line.qty_to_fulfill:g}{uom_str}</li>"
                )

        if not has_any_fulfillment:
            raise UserError(_("Please specify a fulfillment quantity greater than 0 for at least one item."))

        # Update document state
        request._check_fulfillment_status()

        # Log to chatter
        state_label = dict(request._fields['state'].selection).get(request.state, request.state)
        body = _(
            "<p><b>Items Fulfilled:</b></p><ul>%s</ul><p>Document status updated to: <b>%s</b></p>"
        ) % ("".join(fulfilled_details), state_label)
        request.message_post(body=body)

        return {'type': 'ir.actions.act_window_close'}


class ITAssetMaterialRequestFulfillWizardLine(models.TransientModel):
    _name = 'it_asset.material_request.fulfill.wizard.line'
    _description = 'Fulfill Material Request Wizard Line'

    wizard_id = fields.Many2one(
        'it_asset.material_request.fulfill.wizard',
        string='Wizard',
        ondelete='cascade'
    )
    line_id = fields.Many2one(
        'it_asset.material_request.line',
        string='Request Line',
        ondelete='cascade'
    )
    name = fields.Char(string='Item Name', readonly=True)
    uom = fields.Char(string='Unit of Measure', readonly=True)
    qty_requested = fields.Float(string='Requested', readonly=True)
    qty_previously_fulfilled = fields.Float(string='Fulfilled So Far', readonly=True)
    qty_remaining = fields.Float(string='Remaining', readonly=True)
    qty_to_fulfill = fields.Float(string='Fulfill Now', default=0.0)
