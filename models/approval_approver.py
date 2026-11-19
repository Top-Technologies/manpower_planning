from odoo import api, models


class ApprovalApprover(models.Model):
    _inherit = 'approval.approver'

    @api.model_create_multi
    def create(self, vals_list):
        approvers = super().create(vals_list)
        previous = {request.id: 'new' for request in approvers.request_id}
        approvers.request_id._sync_manpower_execution(previous)
        return approvers

    def write(self, vals):
        requests = self.request_id
        previous = {request.id: request.request_status for request in requests}
        res = super().write(vals)
        requests._sync_manpower_execution(previous)
        return res
