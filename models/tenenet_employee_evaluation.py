from odoo import Command, SUPERUSER_ID, _, fields, models
from odoo.exceptions import UserError


class TenenetEmployeeEvaluation(models.Model):
    _inherit = "tenenet.employee.evaluation"

    signed_document_id = fields.Many2one(
        "documents.document",
        string="Podpísané hodnotenie",
        readonly=True,
        copy=False,
    )

    def unlink(self):
        self._unlink_signed_documents()
        return super().unlink()

    def action_open_signed_document(self):
        self.ensure_one()
        if not self.signed_document_id:
            raise UserError(_("Podpísané hodnotenie ešte nebolo uložené do dokumentov."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Podpísané ročné hodnotenie"),
            "res_model": "documents.document",
            "view_mode": "kanban,list,form",
            "domain": [("id", "=", self.signed_document_id.id)],
            "context": {"create": False},
        }

    def _store_signed_document_if_needed(self):
        for evaluation in self.filtered(lambda rec: rec.sign_state == "signed" and rec.sign_request_id):
            if evaluation.signed_document_id:
                continue
            completed_document = evaluation.sign_request_id.sudo().completed_document_ids[:1]
            if not completed_document or not completed_document.file:
                continue
            folder = evaluation._get_or_create_evaluation_documents_folder()
            name = "%s.pdf" % evaluation._get_evaluation_document_base_name()
            attachment = self.env["ir.attachment"].sudo().create({
                "name": name,
                "datas": completed_document.file,
                "type": "binary",
                "mimetype": "application/pdf",
                "res_model": evaluation._name,
                "res_id": evaluation.id,
            })
            document = self.env["documents.document"].sudo().create({
                "name": name,
                "attachment_id": attachment.id,
                "folder_id": folder.id,
                "company_id": evaluation.employee_id.company_id.id or self.env.company.id,
                "partner_id": evaluation.employee_id.work_contact_id.id or evaluation.employee_id.user_id.partner_id.id,
                "owner_id": False,
                "access_via_link": "none",
                "access_internal": "none",
                "is_access_via_link_hidden": True,
                "access_ids": [
                    Command.create({"partner_id": partner.id, "role": "view"})
                    for partner in evaluation._get_signed_document_access_partners()
                ],
            })
            evaluation.sudo().signed_document_id = document.id
            evaluation.message_post(body=_("Podpísané ročné hodnotenie bolo uložené do dokumentov: %s.", document._get_html_link()))

    def _get_or_create_evaluation_documents_folder(self):
        self.ensure_one()
        employee = self.employee_id.sudo()
        if not employee.hr_employee_folder_id:
            employee._generate_employee_documents_folders()
        parent_folder = employee.hr_employee_folder_id
        if not parent_folder:
            raise UserError(_("Pre zamestnanca sa nepodarilo nájsť alebo vytvoriť priečinok v Dokumentoch."))
        folder = self.env["documents.document"].sudo().search([
            ("type", "=", "folder"),
            ("folder_id", "=", parent_folder.id),
            ("name", "=", "Ročné hodnotenia"),
        ], limit=1)
        if folder:
            return folder
        return self.env["documents.document"].sudo().create({
            "name": "Ročné hodnotenia",
            "type": "folder",
            "folder_id": parent_folder.id,
            "company_id": employee.company_id.id,
            "access_internal": "none",
            "access_via_link": "none",
            "is_access_via_link_hidden": True,
            "owner_id": False,
        })

    def _get_signed_document_access_partners(self):
        self.ensure_one()
        partners = (
            self.employee_id.work_contact_id
            | self.employee_id.user_id.partner_id
            | self.manager_id.work_contact_id
            | self.manager_id.user_id.partner_id
        )
        return partners.filtered(lambda partner: partner and partner.id and partner.user_ids != self.env.ref("base.user_root"))

    def _unlink_signed_documents(self):
        Document = self.env["documents.document"].with_user(SUPERUSER_ID)
        documents = Document
        folders = Document
        for evaluation in self.sudo():
            document = Document.browse(evaluation.signed_document_id.id)
            attachment = document.attachment_id
            if (
                document
                and attachment.res_model == evaluation._name
                and attachment.res_id == evaluation.id
            ):
                documents |= document
                if document.folder_id.name == "Ročné hodnotenia":
                    folders |= document.folder_id

        if documents:
            documents.unlink()
        empty_folders = folders.exists().filtered(lambda folder: not folder.with_context(active_test=False).children_ids)
        if empty_folders:
            empty_folders.unlink()
