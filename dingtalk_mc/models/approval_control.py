# -*- coding: utf-8 -*-
# Copyright (C) 2020 SuXueFeng GNU

import logging
from odoo import api, fields, models, http, _
import inspect
from odoo.http import request
import sys
from lxml import etree
from odoo.addons.web.controllers.main import DataSet
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DingTalkApprovalControl(models.Model):
    _name = 'dingtalk.approval.control'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = "审批配置"
    _rec_name = 'name'

    def _compute_domain(self):
        all_cls = inspect.getmembers(sys.modules[__name__], inspect.isclass)
        odoo_cls = [getattr(cls[1], '_name') for cls in all_cls if cls[1].__bases__[0].__name__ == 'Model']   # 排除当前的对象
        odoo_cls += [model.model for model in self.env['ir.model'].search([('transient', '=', True)])]        # 排除临时对象
        return [('model', 'not in', odoo_cls)]

    name = fields.Char('名称', required=1, track_visibility='onchange')
    company_id = fields.Many2one('res.company', string=u'公司', default=lambda self: self.env.user.company_id.id)
    oa_model_id = fields.Many2one('ir.model', string=u'Odoo模型', index=True, ondelete="set null", domain=_compute_domain)
    template_id = fields.Many2one('dingtalk.approval.template', string=u'审批模板', index=True, ondelete="set null", domain="[('company_id', '=', company_id)]")
    template_icon = fields.Html(string='图标', compute='_compute_template_icon')
    ftype = fields.Selection(string=u'单据类型', selection=[('oa', 'OA单据'), ('bus', '业务单据')], default='oa')

    line_ids = fields.One2many(comodel_name='dingtalk.approval.control.line', inverse_name='control_id', string=u'字段详情')
    model_start_button_ids = fields.Many2many('dingtalk.approval.model.button', 'dingtalk_approval_control_model_start_rel',
                                              string=u'审批前禁用功能', domain="[('model_id', '=', oa_model_id), ('company_id', '=', company_id)]")
    model_button_ids = fields.Many2many('dingtalk.approval.model.button', string=u'审批中禁用功能',
                                        domain="[('model_id', '=', oa_model_id), ('company_id', '=', company_id)]")
    model_pass_button_ids = fields.Many2many('dingtalk.approval.model.button', 'dingtalk_approval_control_model_pass_rel',
                                            string=u'审批通过禁用功能', domain="[('model_id', '=', oa_model_id), ('company_id', '=', company_id)]")
    model_end_button_ids = fields.Many2many('dingtalk.approval.model.button', 'dingtalk_approval_control_model_end_rel',
                                            string=u'审批拒绝禁用功能', domain="[('model_id', '=', oa_model_id), ('company_id', '=', company_id)]")

    approval_start_function = fields.Char(string=u'提交审批-执行函数')
    approval_restart_function = fields.Char(string=u'重新提交-执行函数')
    approval_pass_function = fields.Char(string=u'审批通过-执行函数')
    approval_refuse_function = fields.Char(string=u'审批拒绝-执行函数')
    approval_end_function = fields.Char(string=u'审批结束-执行函数')
    is_ing_write = fields.Boolean(string="审批中允许编辑？", default=False)
    is_end_write = fields.Boolean(string="审批结束允许编辑？", default=False)
    remarks = fields.Text(string=u'备注')
    approval_type = fields.Selection(string="审批类型", selection=[('turn', '依次审批'), ('huo', '会签/或签')])
    approval_user_ids = fields.Many2many('hr.employee', 'dingtalk_approval_employee_approval_rel', string="审批人列表", domain="[('ding_id', '!=', '')]")
    huo_approval_user_ids = fields.One2many(comodel_name="dingtalk.approval.huo.user.line", inverse_name="control_id", string="审批列表")
    cc_user_ids = fields.Many2many('hr.employee', 'dingtalk_approval_employee_cc_rel', string="抄送人列表", domain="[('ding_id', '!=', '')]")
    cc_type = fields.Selection(string="抄送时间", selection=[('START', '开始'), ('FINISH', '结束'), ('START_FINISH', '开始和结束')], default='START')

    @api.constrains('oa_model_id', 'company_id')
    def _constrains_unique(self):
        """
        检查唯一性
        :return:
        """
        for res in self:
            result = self.search_count([('company_id', '=', res.company_id.id), ('oa_model_id', '=', res.oa_model_id.id)])
            if result > 1:
                raise UserError(u'公司中已存在Odoo模型对应的审批模板，请勿重复创建！')

    def action_reload_current_page(self):
        """
        配置审批后需要自动升级配置的模型对应的模块，然后刷新界面
        :return:
        """
        if len(self.line_ids) < 1:
            raise UserError("注意：你还没有配置单据对应的字段，请完整配置odoo单据与钉钉单据的字段对应关系，否则提交审批时会失败！")
        module_name = self.oa_model_id.sudo().modules
        module_names = module_name.replace(' ', '').split(',')
        current_module = self.env['ir.module.module'].sudo().search([('name', 'in', module_names)])
        current_module.sudo().button_immediate_upgrade()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    @api.depends('template_id')
    def _compute_template_icon(self):
        for res in self:
            if res.template_id:
                res.template_icon = """<img src="{icon}" width="80px" height="80px">""".format(icon=res.template_id.icon_avatar_url)
            else:
                res.template_icon = False

    def create_approval(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self.oa_model_id.model,
            "views": [[False, "form"]],
            "context": {
                'form_view_initial_mode': 'edit'
            },
        }

    def action_approval_tree(self):
        """
        跳转至审批列表
        :return:
        """
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self.oa_model_id.model,
            "views": [[False, "tree"], [False, "form"]],
            "name": self.oa_model_id.name,
        }

    @api.onchange('oa_model_id')
    def onchange_model_id(self):
        """
        根据选择的模型读取模型动作按钮
        :return:
        """
        for rec in self:
            if rec.oa_model_id:
                model_id = rec.oa_model_id
                result = self.env[model_id.model].fields_view_get()
                root = etree.fromstring(result['arch'])
                for item in root.xpath("//header/button"):
                    domain = [('model_id', '=', model_id.id), ('function', '=', item.get('name')), ('company_id', '=', rec.company_id.id)]
                    model_buts = self.env['dingtalk.approval.model.button'].search(domain)
                    if not model_buts:
                        self.env['dingtalk.approval.model.button'].create({
                            'model_id': model_id.id,
                            'name': item.get('string'),
                            'function': item.get('name'),
                            'modifiers': item.get('modifiers'),
                            'company_id': rec.company_id.id,
                        })

    @api.model
    def create(self, vals):
        # 清除运行函数中的空格
        if vals.get('approval_start_function'):
            vals['approval_start_function'] = vals['approval_start_function'].replace(' ', '')
        if vals.get('approval_pass_function'):
            vals['approval_pass_function'] = vals['approval_pass_function'].replace(' ', '')
        if vals.get('approval_refuse_function'):
            vals['approval_refuse_function'] = vals['approval_refuse_function'].replace(' ', '')
        if vals.get('approval_restart_function'):
            vals['approval_restart_function'] = vals['approval_restart_function'].replace(' ', '')
        return super(DingTalkApprovalControl, self).create(vals)

    def write(self, vals):
        # 清除运行函数中的空格
        if vals.get('approval_start_function'):
            vals['approval_start_function'] = vals['approval_start_function'].replace(' ', '')
        if vals.get('approval_pass_function'):
            vals['approval_pass_function'] = vals['approval_pass_function'].replace(' ', '')
        if vals.get('approval_refuse_function'):
            vals['approval_refuse_function'] = vals['approval_refuse_function'].replace(' ', '')
        if vals.get('approval_restart_function'):
            vals['approval_restart_function'] = vals['approval_restart_function'].replace(' ', '')
        return super(DingTalkApprovalControl, self).write(vals)

    def get_approvers_users(self):
        """
        返回审批人列表
        :return:
        """
        for res in self:
            if not res.approval_type:
                return False
            if res.approval_type == 'turn':
                approval_users = ""
                for approval in res.approval_user_ids:
                    if not approval_users:
                        approval_users = approval.ding_id
                    else:
                        approval_users = "{},{}".format(approval_users, approval.ding_id)
                return approval_users
            if res.approval_type == 'huo':
                approval_users = list()
                for approval in res.huo_approval_user_ids:
                    user_str = ""
                    for user in approval.employee_ids:
                        if not user_str:
                            user_str = user.ding_id
                        else:
                            user_str = "{},{}".format(user_str, user.ding_id)
                    approval_users.append({
                        'user_ids': user_str,
                        'task_action_type': approval.approval_type
                    })
                return approval_users

    def get_cc_users(self):
        """
        返回抄送人列表
        :return:
        """
        for res in self:
            if len(res.cc_user_ids) > 1:
                if not res.cc_type:
                    raise UserError("抄送人和抄送时间均为必填，否则无法传递该参数！")
                cc_user_str = ""
                for cc_user in res.cc_user_ids:
                    if not cc_user_str:
                        cc_user_str = cc_user.ding_id
                    else:
                        cc_user_str = "{},{}".format(cc_user_str, cc_user.ding_id)
                return cc_user_str, res.cc_type
            else:
                return False, False


class DingTalkApprovalControlLine(models.Model):
    _description = "审批配置详情"
    _name = 'dingtalk.approval.control.line'
    _rec_name = 'control_id'

    sequence = fields.Integer(string=u'序号')
    control_id = fields.Many2one(comodel_name='dingtalk.approval.control', string=u'审批配置', ondelete="set null")
    model_id = fields.Many2one(comodel_name='ir.model', string=u'Odoo模型', related="control_id.oa_model_id")
    # field_id = fields.Many2one(comodel_name='ir.model.fields', string=u'模型字段',
    #                            domain="[('model_id', '=', model_id), ('ttype', 'not in', ['binary', 'boolean'])]")
    field_id = fields.Many2one(comodel_name='ir.model.fields', string=u'模型字段', domain="[('model_id', '=', model_id),('name', 'not ilike', 'has_%')]")
    ttype = fields.Selection(selection='_get_field_types', string=u'字段类型')
    dd_field = fields.Char(string='钉钉单据字段名')
    is_dd_id = fields.Boolean(string=u'为关联组件?', help="通常用于钉钉表单上选择的是钉钉提供的组件，比如部门,就需要传递部门id而不是名称")
    list_ids = fields.One2many(comodel_name='dingtalk.approval.control.list', inverse_name='line_id', string=u'一对多列表字段')

    @api.onchange('field_id')
    def _onchange_fisld_id(self):
        for res in self:
            res.dd_field = res.field_id.field_description
            res.ttype = res.field_id.ttype

    @api.model
    def _get_field_types(self):
        ttype = sorted((key, key) for key in fields.MetaField.by_type)
        image_ttype = [('image', 'image'), ('image_url', 'image_url')]
        ttype.extend(image_ttype)
        return ttype
        # return sorted((key, key) for key in fields.MetaField.by_type)


class DingTalkApprovalControlList(models.Model):
    _description = '一对多列表字段'
    _name = 'dingtalk.approval.control.list'
    _rec_name = 'line_id'

    sequence = fields.Integer(string=u'序号')
    line_id = fields.Many2one(comodel_name='dingtalk.approval.control.line', string=u'审批配置详情', ondelete='set null')
    line_field_id = fields.Many2one(comodel_name='ir.model.fields', string=u'字段列表字段')
    field_id = fields.Many2one(comodel_name='ir.model.fields', string=u'模型字段')
    dd_field = fields.Char(string='钉钉单据字段名')
    is_dd_id = fields.Boolean(string=u'关联组件?')

    @api.onchange('line_field_id')
    def onchange_line_field_id(self):
        for rec in self:
            model = self.env['ir.model'].sudo().search([('model', '=', rec.line_field_id.relation)], limit=1)
            domain = [('model_id', '=', model.id), ('ttype', 'not in', ['one2many', 'binary', 'boolean'])]
            return {'domain': {'field_id': domain}}

    @api.onchange('field_id')
    def _onchange_fisld_id(self):
        for res in self:
            res.dd_field = res.field_id.field_description


class DingTalkApprovalUserLine(models.Model):
    _name = 'dingtalk.approval.huo.user.line'
    _description = "会签/或签用户列表"
    _rec_name = 'control_id'

    control_id = fields.Many2one(comodel_name="dingtalk.approval.control", string="审批配置", ondelete='set null')
    employee_ids = fields.Many2many('hr.employee', 'dingtalk_approval_huo_user_list_rel', string="审批人", domain="[('ding_id', '!=', '')]")
    approval_type = fields.Selection(string="审批类型", selection=[('AND', '会签'), ('OR', '或签'), ('NONE', '单人')], required=True, default='NONE')

    @api.constrains('approval_type', 'employee_ids')
    def _constrains_approval_type(self):
        """
        检查是否配置正确
        会签/或签列表长度必须大于1，非会签/或签列表长度只能为1
        :return:
        """
        for res in self:
            if res.approval_type == 'NONE' and len(res.employee_ids) > 1:
                raise UserError("非会签/或签时，审批人的长度只能为1")
            if res.approval_type != 'NONE' and len(res.employee_ids) <= 1:
                raise UserError("会签/或签时，审批人的长度必须大于1")


class DingDingApprovalButton(models.Model):
    _name = 'dingtalk.approval.model.button'
    _description = '钉钉审批模型按钮'
    _rec_name = 'name'

    model_id = fields.Many2one('ir.model', string='模型', index=True)
    model_model = fields.Char(string='模型名', related='model_id.model', store=True, index=True)
    name = fields.Char(string="按钮名称", index=True)
    function = fields.Char(string='按钮方法', index=True)
    modifiers = fields.Char(string="按钮属性值")
    company_id = fields.Many2one('res.company', string=u'公司', default=lambda self: self.env.user.company_id.id)

    def name_get(self):
        return [(rec.id, "%s:%s" % (rec.model_id.name, rec.name)) for rec in self]


class ReturnApprovalState(models.TransientModel):
    _name = 'dingtalk.return.approval.state'
    _description = "恢复单据状态"

    APPROVALSTATE = [('draft', '草稿'), ('approval', '审批中'), ('stop', '审批结束')]
    APPROVALRESULT = [('load', '等待'), ('agree', '同意'), ('refuse', '拒绝'), ('redirect', '转交')]
    
    name = fields.Char(string="单据表名", required=True)
    res_id = fields.Integer(string="记录ID", required=True)
    dd_approval_state = fields.Selection(string="审批状态", selection=APPROVALSTATE, required=True, default='draft')
    dd_approval_result = fields.Selection(string="审批结果", selection=APPROVALRESULT, required=True, default='load')

    def confirm_return(self):
        """
        重置操作
        :return:
        """
        self.ensure_one()
        table_name = self.name.replace('.', '_')
        sql = """update {name} set dd_approval_state='{das}',dd_approval_result='{dar}',dd_doc_state='' where id={id}"""\
            .format(name=table_name, das=self.dd_approval_state, dar=self.dd_approval_result, id=self.res_id)
        _logger.info(sql)
        try:
            self._cr.execute(sql)
        except Exception as e:
            raise UserError("强制重置失败，原因为：{}".format(str(e)))


class DingDingDataSet(DataSet):

    @http.route('/web/dataset/call_button', type='json', auth="user")
    def call_button(self, model, method, args, kwargs):
        ir_model = request.env['ir.model'].sudo().search([('model', '=', model)], limit=1)
        uid = kwargs.get('context').get('uid')
        user = request.env['res.users'].search([('id', '=', uid)])
        domain = [('oa_model_id', '=', ir_model.id), ('company_id', '=', user.company_id.id)]
        approval = request.env['dingtalk.approval.control'].sudo().search(domain, limit=1)
        if approval:
            # 获取当前单据的id
            if args[0]:
                res_id = args[0][0]
            else:
                params = args[1].get('params')
                res_id = params.get('id')
            # 获取当前单据
            now_model = request.env[model].sudo().search([('id', '=', res_id)])
            if now_model and now_model.dd_approval_state == 'draft':
                start_but_functions = list()
                for button in approval.model_start_button_ids:
                    start_but_functions.append(button.function)
                if method in start_but_functions:
                    raise UserError(_("本功能暂无法使用，因为单据还没有'提交至钉钉'进行审批，请先提交至钉钉进行审批后再试！"))
            elif now_model and now_model.dd_approval_state == 'approval':
                but_functions = list()
                for button in approval.model_button_ids:
                    but_functions.append(button.function)
                if method in but_functions:
                    raise UserError(_("本功能暂无法使用，因为单据还是'钉钉审批中'状态。请在单据审批后再试！"))
            elif now_model and now_model.dd_approval_result == 'agree':
                pass_but_functions = list()
                for button in approval.model_pass_button_ids:
                    pass_but_functions.append(button.function)
                if method in pass_but_functions:
                    raise UserError(_("本功能暂无法使用，因为单据已经配置了'审批通过后'不允许使用本功能。"))
            elif now_model and now_model.dd_approval_result == 'refuse':
                end_but_functions = list()
                for button in approval.model_end_button_ids:
                    end_but_functions.append(button.function)
                if method in end_but_functions:
                    raise UserError(_("本功能暂无法使用，因为单据已经配置了'审批拒绝后'不允许使用本功能。"))
        return super(DingDingDataSet, self).call_button(model, method, args, kwargs)