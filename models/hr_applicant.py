from odoo import api, fields, models, _


class HrApplicant(models.Model):
    _inherit = 'hr.applicant'

    manpower_quota_released = fields.Boolean(copy=False, readonly=True)

    def _is_manpower_hired(self):
        self.ensure_one()
        return bool(
            self.active
            and not self.refuse_reason_id
            and (self.date_closed or (self.stage_id and self.stage_id.hired_stage))
        )

    @api.model_create_multi
    def create(self, vals_list):
        applicants = super().create(vals_list)
        for applicant in applicants.filtered(lambda item: item._is_manpower_hired()):
            applicant._release_manpower_quota()
        return applicants

    def write(self, vals):
        if self.env.context.get('skip_manpower_release'):
            return super().write(vals)
        before = {applicant.id: applicant._is_manpower_hired() for applicant in self}
        res = super().write(vals)
        if any(key in vals for key in ('stage_id', 'date_closed', 'active', 'refuse_reason_id')):
            for applicant in self:
                if applicant._is_manpower_hired() and not before.get(applicant.id):
                    applicant._release_manpower_quota()
        return res

    def _release_manpower_quota(self):
        self.ensure_one()
        if self.manpower_quota_released or not self.job_id:
            return
        Request = self.env['approval.request'].sudo()
        base = [
            ('is_manpower_request', '=', True),
            ('request_status', '=', 'approved'),
            ('manpower_executed', '=', True),
            ('manpower_open_headcount', '>', 0),
            ('job_id', '=', self.job_id.id),
            ('company_id', '=', self.company_id.id),
        ]
        request = Request.browse()
        if self.department_id:
            request = Request.search(base + [
                ('plan_scope', '=', 'department'),
                ('department_id', '=', self.department_id.id),
            ], order='date_confirmed, id', limit=1)
            if not request:
                request = Request.search(base + [
                    ('plan_scope', '=', 'project'),
                    ('department_ids', 'in', self.department_id.id),
                ], order='date_confirmed, id', limit=1)
        if not request:
            request = Request.search(base, order='date_confirmed, id', limit=1)
        if not request:
            return
        request.write({'manpower_open_headcount': request.manpower_open_headcount - 1})
        request.message_post(body=_(
            "One approved seat was fulfilled by applicant %s.",
            self.display_name,
        ))
        self.with_context(skip_manpower_release=True).sudo().write({
            'manpower_quota_released': True,
        })
