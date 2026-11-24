from odoo import Command, api, fields, models


class ManpowerDepartmentTarget(models.AbstractModel):
    _name = 'manpower.department.target'
    _description = 'Project department selection'

    department_mode = fields.Selection(
        [
            ('existing', 'Use existing departments'),
            ('create', 'Create a department'),
        ],
        string="Project Departments",
        default='existing',
    )
    new_department_name = fields.Char(string="Department Name")
    department_key = fields.Char(compute='_compute_department_key', store=True)

    @api.depends('plan_scope', 'department_id', 'department_ids')
    def _compute_department_key(self):
        for record in self:
            if record.plan_scope == 'department':
                ids = [record.department_id.id] if record.department_id else []
            else:
                ids = sorted(record.department_ids.ids)
            record.department_key = ','.join(str(department_id) for department_id in ids)

    def _materialize_project_departments(self):
        if self.env.context.get('skip_department_materialize'):
            return
        Department = self.env['hr.department']
        for record in self:
            if record.plan_scope != 'project' or record.department_mode != 'create':
                continue
            name = (record.new_department_name or '').strip()
            if not name:
                continue
            department = Department.search([
                ('name', '=', name),
                ('company_id', '=', record.company_id.id),
            ], limit=1)
            if not department:
                department = Department.create({
                    'name': name,
                    'company_id': record.company_id.id,
                })
            record.with_context(skip_department_materialize=True).write({
                'department_ids': [Command.link(department.id)],
                'new_department_name': False,
                'department_mode': 'existing',
            })
