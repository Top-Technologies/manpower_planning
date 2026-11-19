from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class HrManpowerPlan(models.Model):
    _name = 'hr.manpower.plan'
    _description = 'Manpower Plan'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc, id desc'
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True, tracking=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(related='company_id.currency_id')
    state = fields.Selection(
        [('draft', 'Draft'), ('approved', 'Approved'), ('closed', 'Closed')],
        default='draft', required=True, tracking=True, index=True,
    )
    plan_scope = fields.Selection(
        [('department', 'Departmental'), ('project', 'Project-Based')],
        required=True, tracking=True, index=True,
    )
    department_id = fields.Many2one(
        'hr.department', check_company=True, tracking=True, index=True,
        domain="[('company_id', '=', company_id)]",
    )
    project_id = fields.Many2one(
        'project.project', check_company=True, tracking=True, index=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    date_start = fields.Date(required=True, tracking=True, index=True)
    date_end = fields.Date(required=True, tracking=True, index=True)
    line_ids = fields.One2many('hr.manpower.plan.line', 'plan_id', copy=True)
    request_count = fields.Integer(compute='_compute_request_count')
    can_manage_plan = fields.Boolean(compute='_compute_can_manage_plan')

    @api.depends_context('uid')
    @api.depends('plan_scope', 'project_id.user_id')
    def _compute_can_manage_plan(self):
        is_hr_manager = self.env.user.has_group('hr.group_hr_manager')
        is_project_user = self.env.user.has_group('project.group_project_user')
        for plan in self:
            owns_project = plan.plan_scope != 'department' and (
                not plan.project_id or plan.project_id.user_id == self.env.user
            )
            plan.can_manage_plan = is_hr_manager or (is_project_user and owns_project)

    @api.depends('plan_scope', 'department_id', 'project_id', 'line_ids.job_id', 'company_id')
    def _compute_request_count(self):
        Request = self.env['approval.request'].sudo()
        for plan in self:
            plan.request_count = Request.search_count(plan._request_domain()) if plan.line_ids else 0

    @api.onchange('plan_scope')
    def _onchange_plan_scope(self):
        if self.plan_scope == 'department':
            self.project_id = False
            for line in self.line_ids:
                line.milestone_id = False
                line.phase_start = False
                line.phase_end = False
        elif self.plan_scope == 'project':
            self.department_id = False
            for line in self.line_ids:
                line.contract_type_id = False

    @api.constrains('plan_scope', 'department_id', 'project_id')
    def _check_scope_target(self):
        for plan in self:
            if plan.plan_scope == 'department' and not plan.department_id:
                raise ValidationError(_("A departmental plan needs a department."))
            if plan.plan_scope == 'project' and not plan.project_id:
                raise ValidationError(_("A project plan needs a project."))

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for plan in self:
            if plan.date_start and plan.date_end and plan.date_start > plan.date_end:
                raise ValidationError(_("The plan end date must be on or after the start date."))

    @api.constrains(
        'state', 'plan_scope', 'department_id', 'project_id',
        'date_start', 'date_end', 'company_id',
    )
    def _check_approved_overlap(self):
        for plan in self.filtered(lambda item: item.state == 'approved'):
            domain = [
                ('id', '!=', plan.id),
                ('state', '=', 'approved'),
                ('company_id', '=', plan.company_id.id),
                ('plan_scope', '=', plan.plan_scope),
                ('date_start', '<=', plan.date_end),
                ('date_end', '>=', plan.date_start),
            ]
            if plan.plan_scope == 'department':
                domain.append(('department_id', '=', plan.department_id.id))
            else:
                domain.append(('project_id', '=', plan.project_id.id))
            if self.sudo().search_count(domain):
                raise ValidationError(_(
                    "Another approved manpower plan already covers this period for the same department or project."
                ))

    def _request_domain(self):
        self.ensure_one()
        domain = [
            ('is_manpower_request', '=', True),
            ('company_id', '=', self.company_id.id),
            ('plan_scope', '=', self.plan_scope),
            ('job_id', 'in', self.line_ids.job_id.ids),
        ]
        if self.plan_scope == 'department':
            domain.append(('department_id', '=', self.department_id.id))
        else:
            domain.append(('project_id', '=', self.project_id.id))
        return domain

    def _check_can_manage(self):
        if any(not plan.can_manage_plan for plan in self):
            raise UserError(_("You can update manpower plans for your own projects, or as an HR manager."))

    def action_view_requests(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'manpower_planning.approval_request_action_manpower'
        )
        action['domain'] = self._request_domain()
        action['view_mode'] = 'list,form'
        action['views'] = [
            (self.env.ref('manpower_planning.approval_request_view_list_manpower').id, 'list'),
            (self.env.ref('approvals.approval_request_view_form').id, 'form'),
        ]
        action.pop('res_id', None)
        context = {
            'active_id': False,
            'active_ids': [],
            'active_model': 'approval.request',
            'default_plan_scope': self.plan_scope,
            'default_company_id': self.company_id.id,
        }
        category = self.env['approval.category'].search([
            ('is_manpower_request', '=', True),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if category:
            context['default_category_id'] = category.id
        if self.plan_scope == 'department':
            context['default_department_id'] = self.department_id.id
        elif self.plan_scope == 'project':
            context['default_project_id'] = self.project_id.id
        action['context'] = context
        return action

    def action_mark_approved(self):
        self._check_can_manage()
        for plan in self:
            if plan.state != 'draft':
                raise UserError(_("Only a draft plan can be approved."))
            if not plan.line_ids:
                raise UserError(_("Add at least one job line before approving the plan."))
        self.write({'state': 'approved'})

    def action_close(self):
        self._check_can_manage()
        if any(plan.state != 'approved' for plan in self):
            raise UserError(_("Only an approved plan can be closed."))
        self.write({'state': 'closed'})

    def action_reset_draft(self):
        self._check_can_manage()
        self.write({'state': 'draft'})


class HrManpowerPlanLine(models.Model):
    _name = 'hr.manpower.plan.line'
    _description = 'Manpower Plan Line'
    _order = 'job_id, id'
    _check_company_auto = True

    plan_id = fields.Many2one('hr.manpower.plan', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='plan_id.company_id', store=True)
    currency_id = fields.Many2one(related='plan_id.currency_id')
    plan_scope = fields.Selection(related='plan_id.plan_scope')
    project_id = fields.Many2one(related='plan_id.project_id')
    job_id = fields.Many2one(
        'hr.job', required=True, check_company=True, index=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    contract_type_id = fields.Many2one('hr.contract.type', string="Contract Type")
    milestone_id = fields.Many2one(
        'project.milestone', string="Target Phase",
        domain="[('project_id', '=', project_id)]",
    )
    phase_start = fields.Date(string="Phase Start")
    phase_end = fields.Date(string="Phase End")
    planned_headcount = fields.Integer(string="Planned Headcount", required=True, default=0)
    budgeted_salary = fields.Monetary(currency_field='currency_id', groups='hr.group_hr_manager')
    note = fields.Text()

    _planned_headcount_positive = models.Constraint(
        'CHECK(planned_headcount >= 0)',
        'Planned headcount cannot be negative.',
    )

    @api.onchange('milestone_id')
    def _onchange_milestone_id(self):
        if self.milestone_id.deadline and not self.phase_end:
            self.phase_end = self.milestone_id.deadline

    def _covers_date(self, day):
        self.ensure_one()
        if self.phase_start and day < self.phase_start:
            return False
        if self.phase_end and day > self.phase_end:
            return False
        return True

    @api.constrains('contract_type_id', 'plan_id')
    def _check_contract_type(self):
        for line in self:
            if line.plan_id.plan_scope == 'department' and not line.contract_type_id:
                raise ValidationError(_("A department plan line needs a contract type."))

    @api.constrains('phase_start', 'phase_end', 'plan_id')
    def _check_phase_dates(self):
        for line in self:
            if line.phase_start and line.phase_end and line.phase_start > line.phase_end:
                raise ValidationError(_("The phase end must be on or after the phase start."))
            plan = line.plan_id
            if line.phase_start and plan.date_start and line.phase_start < plan.date_start:
                raise ValidationError(_("The phase must start within the plan period."))
            if line.phase_end and plan.date_end and line.phase_end > plan.date_end:
                raise ValidationError(_("The phase must end within the plan period."))

    @api.constrains('plan_id', 'job_id', 'contract_type_id', 'milestone_id')
    def _check_line_unique(self):
        for line in self:
            domain = [
                ('id', '!=', line.id),
                ('plan_id', '=', line.plan_id.id),
                ('job_id', '=', line.job_id.id),
            ]
            if line.plan_id.plan_scope == 'department':
                domain.append(('contract_type_id', '=', line.contract_type_id.id))
                label = _("job and contract type")
            else:
                domain.append(('milestone_id', '=', line.milestone_id.id or False))
                label = _("job and target phase")
            if self.search_count(domain):
                raise ValidationError(_("Each %s can appear only once on a manpower plan.", label))
