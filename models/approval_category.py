from odoo import api, fields, models

from odoo.addons.approvals.models.approval_category import CATEGORY_SELECTION

MANPOWER_FLAG_FIELDS = (
    'has_manpower_scope',
    'has_manpower_department',
    'has_manpower_project',
    'has_manpower_job',
    'has_manpower_headcount',
    'has_manpower_justification',
)


class ApprovalCategory(models.Model):
    _inherit = 'approval.category'

    is_manpower_request = fields.Boolean(
        string="Manpower Request",
        help="Requests in this category carry a manpower requisition and show the plan check.",
    )
    has_manpower_scope = fields.Selection(
        CATEGORY_SELECTION, string="Manpower Scope", default='no', required=True,
    )
    has_manpower_department = fields.Selection(
        CATEGORY_SELECTION, string="Manpower Department", default='no', required=True,
    )
    has_manpower_project = fields.Selection(
        CATEGORY_SELECTION, string="Manpower Project", default='no', required=True,
    )
    has_manpower_job = fields.Selection(
        CATEGORY_SELECTION, string="Manpower Job", default='no', required=True,
    )
    has_manpower_headcount = fields.Selection(
        CATEGORY_SELECTION, string="Requested Headcount", default='no', required=True,
    )
    has_manpower_justification = fields.Selection(
        CATEGORY_SELECTION, string="Justification", default='no', required=True,
    )

    @api.onchange('is_manpower_request')
    def _onchange_is_manpower_request(self):
        if self.is_manpower_request:
            for field_name in MANPOWER_FLAG_FIELDS:
                if self[field_name] == 'no':
                    self[field_name] = 'required'

    @api.model
    def _manpower_category_vals(self, company):
        return {
            'name': 'Manpower Request',
            'company_id': company.id,
            'description': 'Manpower requisition against an approved manpower plan.',
            'sequence': 1,
            'is_manpower_request': True,
            'has_manpower_scope': 'required',
            'has_manpower_department': 'required',
            'has_manpower_project': 'required',
            'has_manpower_job': 'required',
            'has_manpower_headcount': 'required',
            'has_manpower_justification': 'required',
            'manager_approval': 'required',
            'approver_sequence': True,
            'approval_minimum': 1,
            'automated_sequence': True,
            'sequence_code': 'MP',
            'requirer_document': 'optional',
        }

    @api.model
    def _ensure_manpower_categories(self, companies=None):
        companies = companies or self.env['res.company'].sudo().search([])
        existing = set(self.sudo().search([
            ('is_manpower_request', '=', True),
            ('company_id', 'in', companies.ids),
        ]).mapped('company_id').ids)
        vals_list = [
            self._manpower_category_vals(company)
            for company in companies
            if company.id not in existing
        ]
        if vals_list:
            self.sudo().create(vals_list)
