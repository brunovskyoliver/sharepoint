from odoo import models


class SignRequest(models.Model):
    _inherit = "sign.request"

    def _sign(self):
        res = super()._sign()
        evaluation_ids = [
            request.reference_doc.id
            for request in self
            if request.reference_doc
            and request.reference_doc._name == "tenenet.employee.evaluation"
        ]
        evaluations = self.env["tenenet.employee.evaluation"].browse(evaluation_ids)
        evaluations._store_signed_document_if_needed()
        return res
