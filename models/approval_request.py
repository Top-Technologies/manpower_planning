from markupsafe import Markup, escape

from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .manpower_fill import FILL_TYPES_BY_SCOPE


class ApprovalRequest(models.Model):
    _inherit = 'approval.request'

    is_manpower_request = fields.Boolean(
        related='category_id.is_manpower_request', store=True, index=True,
    )
    has_manpower_scope = fields.Selection(related='category_id.has_manpower_scope')
    has_manpower_department = fields.Selection(related='category_id.has_manpower_department')
    has_manpower_project = fields.Selection(related='category_id.has_manpower_project')
    has_manpower_job = fields.Selection(related='category_id.has_manpower_job')
    has_manpower_headcount = fields.Selection(
        related='category_id.has_manpower_headcount', string="Headcount Field",
    )
    has_manpower_justification = fields.Selection(related='category_id.has_manpower_justification')

    plan_scope = fields.Selection(
        [('department', 'Department'), ('project', 'Project')],
        tracking=True,
    )
    department_id = fields.Many2one(
        'hr.department', tracking=True, check_company=True,
        domain="[('company_id', '=', company_id)]",
    )
    project_id = fields.Many2one(
        'project.project', tracking=True, check_company=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    job_id = fields.Many2one(
        'hr.job', tracking=True, check_company=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    contract_type_id = fields.Many2one('hr.contract.type', string="Contract Type", tracking=True)
    milestone_id = fields.Many2one(
        'project.milestone', string="Target Phase", tracking=True,
        domain="[('project_id', '=', project_id)]",
    )
    requested_headcount = fields.Integer(default=1, tracking=True)
    justification_reason = fields.Selection(
        [
            ('replacement', 'Replacement'),
            ('expansion', 'New / Expansion'),
            ('project_ramp', 'Project Ramp-up'),
        ],
        tracking=True,
    )
    manpower_executed = fields.Boolean(copy=False, readonly=True)
    manpower_unfilled_headcount = fields.Integer(
        copy=False, readonly=True,
        help="Approved seats that HR has not yet filled or opened for recruitment.",
    )
    manpower_open_headcount = fields.Integer(
        copy=False, readonly=True,
        help="Seats sent to recruitment and not yet filled by a hired applicant.",
    )
    manpower_seat_ids = fields.One2many('manpower.request.seat', 'request_id', copy=False)

    active_plan_id = fields.Many2one(
        'hr.manpower.plan', compute='_compute_manpower_quota', compute_sudo=True,
    )
    active_plan_name = fields.Char(compute='_compute_manpower_quota', compute_sudo=True)
    planned_headcount = fields.Integer(compute='_compute_manpower_quota', compute_sudo=True)
    actual_headcount = fields.Integer(compute='_compute_manpower_quota', compute_sudo=True)
    applicant_headcount = fields.Integer(compute='_compute_manpower_quota', compute_sudo=True)
    pending_request_headcount = fields.Integer(compute='_compute_manpower_quota', compute_sudo=True)
    approved_open_headcount = fields.Integer(compute='_compute_manpower_quota', compute_sudo=True)
    pipeline_headcount = fields.Integer(compute='_compute_manpower_quota', compute_sudo=True)
    remaining_quota = fields.Integer(compute='_compute_manpower_quota', compute_sudo=True)
    forecast_balance = fields.Integer(compute='_compute_manpower_quota', compute_sudo=True)
    quota_status = fields.Selection(
        [
            ('within_plan', 'Within Plan'),
            ('exceeds_plan', 'Over-Quota / Exceeds Plan'),
            ('no_plan', 'No Active Plan Found'),
        ],
        compute='_compute_manpower_quota',
        compute_sudo=True,
    )

    @api.onchange('plan_scope')
    def _onchange_plan_scope(self):
        if self.plan_scope == 'department':
            self.project_id = False
            self.milestone_id = False
        elif self.plan_scope == 'project':
            self.department_id = False
            self.contract_type_id = False

    @api.onchange('project_id')
    def _onchange_project_id(self):
        if self.milestone_id and self.milestone_id.project_id != self.project_id:
            self.milestone_id = False

    def _manpower_inputs_ready(self):
        self.ensure_one()
        if not self.is_manpower_request or not self.job_id or not self.plan_scope:
            return False
        if self.plan_scope == 'department':
            return bool(self.department_id and self.contract_type_id)
        return bool(self.project_id)

    def _manpower_zero_vals(self, is_manpower):
        return {
            'active_plan_id': False,
            'active_plan_name': False,
            'planned_headcount': 0,
            'actual_headcount': 0,
            'applicant_headcount': 0,
            'pending_request_headcount': 0,
            'approved_open_headcount': 0,
            'pipeline_headcount': 0,
            'remaining_quota': 0,
            'forecast_balance': 0,
            'quota_status': 'no_plan' if is_manpower else False,
        }

    @api.depends(
        'is_manpower_request', 'plan_scope', 'department_id', 'project_id',
        'job_id', 'contract_type_id', 'milestone_id', 'requested_headcount',
        'company_id', 'request_status', 'manpower_open_headcount',
        'manpower_unfilled_headcount',
    )
    def _compute_manpower_quota(self):
        today = fields.Date.context_today(self)
        for request in self:
            vals = request._manpower_zero_vals(request.is_manpower_request)
            if request._manpower_inputs_ready():
                plan = request._find_active_manpower_plan(today)
                line = request._matching_plan_line(plan, today)
                actual = request._manpower_actual_headcount()
                applicants = request._manpower_applicant_headcount()
                pending = request._manpower_pending_headcount()
                approved_open = request._manpower_approved_open_headcount()
                pipeline = applicants + pending + approved_open
                planned = line.planned_headcount if line else 0
                if request.request_status in ('new', 'pending'):
                    own_hold = request.requested_headcount or 0
                elif request.request_status == 'approved':
                    own_hold = (request.manpower_open_headcount or 0) + (request.manpower_unfilled_headcount or 0)
                else:
                    own_hold = 0
                remaining = planned - actual - pipeline
                balance = remaining - own_hold
                vals.update({
                    'active_plan_id': plan.id if line else False,
                    'active_plan_name': plan.display_name if line else False,
                    'planned_headcount': planned,
                    'actual_headcount': actual,
                    'applicant_headcount': applicants,
                    'pending_request_headcount': pending,
                    'approved_open_headcount': approved_open,
                    'pipeline_headcount': pipeline,
                    'remaining_quota': remaining,
                    'forecast_balance': balance,
                    'quota_status': (
                        'within_plan' if line and balance >= 0 else
                        'exceeds_plan' if line else
                        'no_plan'
                    ),
                })
            elif request.is_manpower_request:
                vals['forecast_balance'] = -(request.requested_headcount or 0)
            request.update(vals)

    def _find_active_manpower_plan(self, today):
        self.ensure_one()
        domain = [
            ('state', '=', 'approved'),
            ('company_id', '=', self.company_id.id),
            ('plan_scope', '=', self.plan_scope),
            ('date_start', '<=', today),
            ('date_end', '>=', today),
        ]
        if self.plan_scope == 'department':
            domain.append(('department_id', '=', self.department_id.id))
        else:
            domain.append(('project_id', '=', self.project_id.id))
        return self.env['hr.manpower.plan'].search(domain, order='date_start desc, id desc', limit=1)

    def _matching_plan_line(self, plan, day):
        self.ensure_one()
        lines = plan.line_ids.filtered(lambda line: line.job_id == self.job_id)
        if self.plan_scope == 'department':
            lines = lines.filtered(lambda line: line.contract_type_id == self.contract_type_id)
        else:
            lines = lines.filtered(
                lambda line: line.milestone_id == self.milestone_id and line._covers_date(day)
            )
        return lines[:1]

    def _manpower_actual_headcount(self):
        """Current seats already filled.

        Department: active employees in that department, job, and contract type.
        Project: active employees in that job assigned to the project. When a
        target phase is set, only assignees of active tasks on that milestone
        are counted. Each employee is counted once.
        """
        self.ensure_one()
        domain = [
            ('active', '=', True),
            ('company_id', '=', self.company_id.id),
            ('job_id', '=', self.job_id.id),
        ]
        if self.plan_scope == 'department':
            domain.append(('department_id', '=', self.department_id.id))
            if self.contract_type_id:
                domain.append(('contract_type_id', '=', self.contract_type_id.id))
            return self.env['hr.employee'].sudo().search_count(domain)
        task_domain = [
            ('project_id', '=', self.project_id.id),
            ('active', '=', True),
        ]
        if self.milestone_id:
            task_domain.append(('milestone_id', '=', self.milestone_id.id))
        tasks = self.env['project.task'].search(task_domain)
        user_ids = set(tasks.user_ids.ids)
        if not self.milestone_id and self.project_id.user_id:
            user_ids.add(self.project_id.user_id.id)
        if not user_ids:
            return 0
        domain.append(('user_id', 'in', list(user_ids)))
        return self.env['hr.employee'].sudo().search_count(domain)

    def _manpower_applicant_headcount(self):
        self.ensure_one()
        domain = [
            ('active', '=', True),
            ('job_id', '=', self.job_id.id),
            ('company_id', '=', self.company_id.id),
            ('refuse_reason_id', '=', False),
            ('date_closed', '=', False),
            ('stage_id.hired_stage', '=', False),
        ]
        if self.plan_scope == 'department':
            domain.append(('department_id', '=', self.department_id.id))
        return self.env['hr.applicant'].search_count(domain)

    def _sibling_request_domain(self):
        self.ensure_one()
        domain = [
            ('is_manpower_request', '=', True),
            ('company_id', '=', self.company_id.id),
            ('plan_scope', '=', self.plan_scope),
            ('job_id', '=', self.job_id.id),
        ]
        if isinstance(self.id, int):
            domain.append(('id', '!=', self.id))
        if self.plan_scope == 'department':
            domain.append(('department_id', '=', self.department_id.id))
            domain.append(('contract_type_id', '=', self.contract_type_id.id))
        else:
            domain.append(('project_id', '=', self.project_id.id))
            domain.append(('milestone_id', '=', self.milestone_id.id or False))
        return domain

    def _sum_sibling_requests(self, extra_domain, field_name):
        self.ensure_one()
        rows = self.env['approval.request']._read_group(
            self._sibling_request_domain() + extra_domain,
            [],
            ['%s:sum' % field_name],
        )
        return (rows[0][0] or 0) if rows else 0

    def _manpower_pending_headcount(self):
        return self._sum_sibling_requests(
            [('request_status', '=', 'pending')],
            'requested_headcount',
        )

    def _manpower_approved_open_headcount(self):
        recruitment = self._sum_sibling_requests(
            [('request_status', '=', 'approved'), ('manpower_open_headcount', '>', 0)],
            'manpower_open_headcount',
        )
        waiting = self._sum_sibling_requests(
            [('request_status', '=', 'approved'), ('manpower_unfilled_headcount', '>', 0)],
            'manpower_unfilled_headcount',
        )
        return recruitment + waiting

    def _check_manpower_ready(self):
        self.ensure_one()
        labels = []
        if not self.plan_scope:
            labels.append(_("Scope"))
        if self.plan_scope == 'department' and not self.department_id:
            labels.append(_("Department"))
        if self.plan_scope == 'department' and not self.contract_type_id:
            labels.append(_("Contract Type"))
        if self.plan_scope == 'project' and not self.project_id:
            labels.append(_("Project"))
        if not self.job_id:
            labels.append(_("Job Position"))
        if not self.justification_reason:
            labels.append(_("Justification"))
        if labels:
            raise ValidationError(_(
                "Complete the manpower request before submitting: %s.",
                ", ".join(labels),
            ))
        if self.requested_headcount < 1:
            raise ValidationError(_("Requested headcount must be at least 1."))

    def action_confirm(self):
        for request in self:
            if request.is_manpower_request:
                request._check_manpower_ready()
        return super().action_confirm()

    def _sync_manpower_execution(self, previous_status):
        for request in self.filtered('is_manpower_request'):
            old = previous_status.get(request.id)
            if old != 'approved' and request.request_status == 'approved':
                request._reserve_manpower_seats()
            elif old == 'approved' and request.request_status in ('cancel', 'refused'):
                request._release_unfilled_manpower_seats()
                if request.manpower_executed:
                    request._warn_manpower_target_unchanged()

    def _reserve_manpower_seats(self):
        self.ensure_one()
        unfilled = max(self.requested_headcount - len(self.manpower_seat_ids), 0)
        self.manpower_unfilled_headcount = unfilled
        if unfilled:
            self.message_post(body=_(
                "%(qty)s seat(s) are waiting. Use Fill seats to record a project transfer, "
                "an internal transfer, a promotion, or to open the job.",
                qty=unfilled,
            ))

    def _release_unfilled_manpower_seats(self):
        self.ensure_one()
        if not self.manpower_unfilled_headcount:
            return
        quantity = self.manpower_unfilled_headcount
        self.manpower_unfilled_headcount = 0
        self.message_post(body=_(
            "%(qty)s seat(s) were still empty. They were released. "
            "Employee moves already applied were left in place.",
            qty=quantity,
        ))

    def action_fill_manpower_seats(self):
        self.ensure_one()
        if not self.env.user.has_group('hr.group_hr_user'):
            raise UserError(_("Only HR can fill manpower seats."))
        if self.request_status != 'approved' or self.manpower_unfilled_headcount < 1:
            raise UserError(_("There is no empty seat to fill."))
        wizard = self.env['manpower.request.fill'].create({
            'request_id': self.id,
            'line_ids': [Command.create({}) for _index in range(self.manpower_unfilled_headcount)],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Fill seats'),
            'res_model': 'manpower.request.fill',
            'view_mode': 'form',
            'res_id': wizard.id,
            'target': 'new',
        }

    def apply_manpower_fill(self, choices):
        self.ensure_one()
        if self.request_status != 'approved':
            raise UserError(_("Fill seats is available after the request is approved."))
        chosen = [(fill_type, employee) for fill_type, employee in choices if fill_type]
        if not chosen:
            raise UserError(_("Choose how to fill at least one seat. An empty row stays open."))
        if len(chosen) > self.manpower_unfilled_headcount:
            raise UserError(_(
                "Only %(qty)s seat(s) are still open.",
                qty=self.manpower_unfilled_headcount,
            ))
        seen = self.env['hr.employee']
        for fill_type, employee in chosen:
            self._check_fill_choice(fill_type, employee)
            if fill_type != 'open_job' and employee in seen:
                raise UserError(_("%s is selected on more than one seat.", employee.display_name))
            if fill_type != 'open_job':
                seen |= employee
        open_quantity = 0
        for fill_type, employee in chosen:
            if fill_type == 'open_job':
                open_quantity += 1
                self.env['manpower.request.seat'].sudo().create({
                    'request_id': self.id,
                    'fill_type': 'open_job',
                })
                continue
            self._apply_employee_fill(fill_type, employee)
            self.env['manpower.request.seat'].sudo().create({
                'request_id': self.id,
                'fill_type': fill_type,
                'employee_id': employee.id,
            })
        self.sudo().write({
            'manpower_unfilled_headcount': self.manpower_unfilled_headcount - len(chosen),
        })
        if open_quantity:
            self._open_manpower_recruitment(open_quantity)

    def _check_fill_choice(self, fill_type, employee):
        self.ensure_one()
        allowed = FILL_TYPES_BY_SCOPE.get(self.plan_scope, ())
        if fill_type not in allowed:
            if self.plan_scope == 'project':
                raise UserError(_("A project request accepts a project transfer, a promotion, or opening the job."))
            raise UserError(_("A department request accepts an internal transfer, a promotion, or opening the job."))
        if fill_type == 'open_job':
            return
        if not employee:
            raise UserError(_("Select the employee for this seat."))
        if not employee.active or employee.company_id != self.company_id:
            raise UserError(_("%s is not an active employee of this company.", employee.display_name))
        if self.manpower_seat_ids.filtered(lambda seat: seat.employee_id == employee):
            raise UserError(_("%s already fills a seat on this request.", employee.display_name))
        if fill_type == 'promotion':
            if employee.job_id == self.job_id:
                raise UserError(_(
                    "%(employee)s is already %(job)s. Use a transfer, or open the job.",
                    employee=employee.display_name,
                    job=self.job_id.display_name,
                ))
        elif employee.job_id != self.job_id:
            raise UserError(_(
                "%(employee)s is %(job)s. Use Promotion to change the job to %(target)s.",
                employee=employee.display_name,
                job=employee.job_id.display_name or _("without a job"),
                target=self.job_id.display_name,
            ))
        if self.plan_scope == 'project' and not employee.user_id:
            raise UserError(_(
                "%s needs a related user. Project headcount counts employees assigned on the project through their user.",
                employee.display_name,
            ))
        if fill_type == 'project_transfer' and self._employee_on_target_project(employee):
            raise UserError(_(
                "%(employee)s is already assigned to %(project)s.",
                employee=employee.display_name,
                project=self.project_id.display_name,
            ))
        if fill_type == 'internal_transfer':
            employee_sudo = employee.sudo()
            same_department = employee_sudo.department_id == self.department_id
            same_contract = (
                not self.contract_type_id
                or employee_sudo.contract_type_id == self.contract_type_id
            )
            if same_department and same_contract:
                raise UserError(_(
                    "%(employee)s is already in %(department)s for this contract type.",
                    employee=employee.display_name,
                    department=self.department_id.display_name,
                ))

    def _apply_employee_fill(self, fill_type, employee):
        self.ensure_one()
        if fill_type == 'internal_transfer':
            vals = {'department_id': self.department_id.id}
            if self.contract_type_id:
                vals['contract_type_id'] = self.contract_type_id.id
            employee.sudo().write(vals)
            employee.message_post(body=_(
                "Internal transfer to %(department)s from manpower request %(request)s.",
                department=self.department_id.display_name,
                request=self.display_name,
            ))
            self.message_post(body=_(
                "%(employee)s filled one seat by internal transfer to %(department)s.",
                employee=employee.display_name,
                department=self.department_id.display_name,
            ))
            return
        if fill_type == 'promotion':
            vals = {'job_id': self.job_id.id}
            if self.plan_scope == 'department':
                vals['department_id'] = self.department_id.id
                if self.contract_type_id:
                    vals['contract_type_id'] = self.contract_type_id.id
            employee.sudo().write(vals)
            if self.plan_scope == 'project':
                self._move_employee_onto_project(employee)
            employee.message_post(body=_(
                "Promoted to %(job)s from manpower request %(request)s.",
                job=self.job_id.display_name,
                request=self.display_name,
            ))
            if self.plan_scope == 'project':
                self.message_post(body=_(
                    "%(employee)s filled one seat by promotion to %(job)s on %(project)s.",
                    employee=employee.display_name,
                    job=self.job_id.display_name,
                    project=self.project_id.display_name,
                ))
            else:
                self.message_post(body=_(
                    "%(employee)s filled one seat by promotion to %(job)s.",
                    employee=employee.display_name,
                    job=self.job_id.display_name,
                ))
            return
        source_names = self._move_employee_onto_project(employee)
        employee.message_post(body=_(
            "Transferred to project %(project)s from manpower request %(request)s.",
            project=self.project_id.display_name,
            request=self.display_name,
        ))
        if source_names:
            self.message_post(body=_(
                "%(employee)s filled one seat by project transfer from %(source)s to %(project)s.",
                employee=employee.display_name,
                source=", ".join(source_names),
                project=self.project_id.display_name,
            ))
        else:
            self.message_post(body=_(
                "%(employee)s filled one seat by project transfer to %(project)s.",
                employee=employee.display_name,
                project=self.project_id.display_name,
            ))

    def _employee_on_target_project(self, employee):
        self.ensure_one()
        user = employee.user_id
        if not user:
            return False
        domain = [
            ('project_id', '=', self.project_id.id),
            ('active', '=', True),
            ('user_ids', 'in', user.id),
        ]
        if self.milestone_id:
            domain.append(('milestone_id', '=', self.milestone_id.id))
        if self.env['project.task'].sudo().search_count(domain):
            return True
        return not self.milestone_id and self.project_id.user_id == user

    def _move_employee_onto_project(self, employee):
        """Assign the employee to this project and take them off other projects.

        Returns the names of the projects they left.
        """
        self.ensure_one()
        user = employee.user_id
        if not user:
            raise UserError(_(
                "%s needs a related user. Project headcount counts employees assigned on the project through their user.",
                employee.display_name,
            ))
        Task = self.env['project.task'].sudo()
        Project = self.env['project.project'].sudo()
        other_tasks = Task.search([
            ('active', '=', True),
            ('user_ids', 'in', user.id),
            ('project_id', '!=', False),
            ('project_id', '!=', self.project_id.id),
            ('company_id', '=', self.company_id.id),
        ])
        source_projects = other_tasks.project_id
        for task in other_tasks:
            task.write({'user_ids': [Command.unlink(user.id)]})
        managed = Project.search([
            ('user_id', '=', user.id),
            ('id', '!=', self.project_id.id),
            ('company_id', '=', self.company_id.id),
        ])
        for project in managed:
            project.user_id = False
            project.message_post(body=_(
                "%(employee)s is no longer the project manager after manpower request %(request)s.",
                employee=employee.display_name,
                request=self.display_name,
            ))
        source_projects |= managed
        if self.milestone_id:
            other_phase_tasks = Task.search([
                ('active', '=', True),
                ('project_id', '=', self.project_id.id),
                ('user_ids', 'in', user.id),
                ('milestone_id', '!=', self.milestone_id.id),
            ])
            for task in other_phase_tasks:
                task.write({'user_ids': [Command.unlink(user.id)]})
        domain = [
            ('project_id', '=', self.project_id.id),
            ('active', '=', True),
        ]
        if self.milestone_id:
            domain.append(('milestone_id', '=', self.milestone_id.id))
        task = Task.search(domain + [('user_ids', 'in', user.id)], limit=1)
        if not task:
            task = Task.search(domain, limit=1)
            if task:
                task.write({'user_ids': [Command.link(user.id)]})
            else:
                vals = {
                    'name': _("Manpower assignment: %s", employee.name),
                    'project_id': self.project_id.id,
                    'user_ids': [Command.set([user.id])],
                }
                if self.milestone_id:
                    vals['milestone_id'] = self.milestone_id.id
                Task.create(vals)
        return source_projects.mapped('display_name')

    def _open_manpower_recruitment(self, quantity):
        self.ensure_one()
        if quantity <= 0 or not self.job_id:
            return
        job = self.job_id.sudo()
        previous = job.no_of_recruitment
        opened = previous <= 0
        if not job.active:
            job.active = True
        job.no_of_recruitment = previous + quantity
        self.sudo().write({
            'manpower_executed': True,
            'manpower_open_headcount': (self.manpower_open_headcount or 0) + quantity,
        })
        link = Markup(
            '<a href="#" data-oe-model="approval.request" data-oe-id="%s">%s</a>'
        ) % (self.id, escape(self.display_name))
        sentence = _(
            "Recruitment target increased by %(qty)s, from %(old)s to %(new)s.",
            qty=quantity,
            old=previous,
            new=job.no_of_recruitment,
        )
        if opened:
            sentence = "%s %s" % (sentence, _("The job position is now open for recruitment."))
        if self.plan_scope == 'department' and self.contract_type_id:
            sentence = "%s %s" % (sentence, _(
                "Contract type: %s.",
                self.contract_type_id.display_name,
            ))
        elif self.milestone_id:
            sentence = "%s %s" % (sentence, _(
                "Target phase: %s.",
                self.milestone_id.display_name,
            ))
        job.message_post(body=Markup("<p>%s %s</p>") % (escape(sentence), link))
        self.message_post(body=_(
            "Recruitment target for %(job)s increased by %(qty)s.",
            job=job.display_name,
            qty=quantity,
        ))

    def _warn_manpower_target_unchanged(self):
        self.ensure_one()
        body = _(
            "This manpower request left the approved state. The recruitment target on %(job)s was left unchanged; "
            "%(open)s approved seat(s) are still open on this request.",
            job=self.job_id.display_name,
            open=self.manpower_open_headcount,
        )
        self.message_post(body=body)
        if self.job_id:
            self.job_id.sudo().message_post(body=body)

    def action_open_active_plan(self):
        self.ensure_one()
        if not self.active_plan_id:
            raise UserError(_("No active manpower plan matches this request."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hr.manpower.plan',
            'view_mode': 'form',
            'res_id': self.active_plan_id.id,
        }
