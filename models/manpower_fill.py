from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError


FILL_TYPE_SELECTION = [
    ('project_transfer', 'Project transfer'),
    ('internal_transfer', 'Internal transfer'),
    ('promotion', 'Promotion'),
    ('open_job', 'Open the job'),
]

FILL_TYPES_BY_SCOPE = {
    'project': ('project_transfer', 'promotion', 'open_job'),
    'department': ('internal_transfer', 'promotion', 'open_job'),
}


class ManpowerRequestSeat(models.Model):
    _name = 'manpower.request.seat'
    _description = 'Filled manpower seat'
    _order = 'id'

    request_id = fields.Many2one(
        'approval.request', required=True, ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(related='request_id.company_id', store=True, index=True)
    fill_type = fields.Selection(FILL_TYPE_SELECTION, required=True)
    employee_id = fields.Many2one('hr.employee', check_company=True)

    _employee_once_per_request = models.UniqueIndex(
        '(request_id, employee_id) WHERE employee_id IS NOT NULL',
        'This employee already fills a seat on the request.',
    )


class ManpowerRequestFill(models.TransientModel):
    _name = 'manpower.request.fill'
    _description = 'Fill manpower seats'

    request_id = fields.Many2one('approval.request', required=True, readonly=True)
    plan_scope = fields.Selection(related='request_id.plan_scope')
    line_ids = fields.One2many('manpower.request.fill.line', 'wizard_id')

    def action_apply(self):
        self.ensure_one()
        if not self.env.user.has_group('hr.group_hr_user'):
            raise UserError(_("Only HR can fill manpower seats."))
        self.request_id.apply_manpower_fill([
            (line.fill_type, line.employee_id) for line in self.line_ids
        ])
        return {'type': 'ir.actions.act_window_close'}


class ManpowerRequestFillLine(models.TransientModel):
    _name = 'manpower.request.fill.line'
    _description = 'Manpower seat to fill'

    wizard_id = fields.Many2one('manpower.request.fill', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='wizard_id.request_id.company_id')
    fill_type = fields.Selection(FILL_TYPE_SELECTION)
    employee_id = fields.Many2one(
        'hr.employee',
        domain="[('company_id', '=', company_id), ('active', '=', True)]",
    )

    @api.onchange('fill_type')
    def _onchange_fill_type(self):
        if self.fill_type == 'open_job':
            self.employee_id = False
