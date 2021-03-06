# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe, json
from frappe.utils import cstr, cint
from frappe import msgprint, _
from frappe.model.mapper import get_mapped_doc

from erpnext.utilities.transaction_base import TransactionBase

subject_field = "title"
sender_field = "contact_email"

class Opportunity(TransactionBase):
	def after_insert(self):
		if self.lead:
			frappe.get_doc("Lead", self.lead).set_status(update=True)

	def validate(self):
		self._prev = frappe._dict({
			"contact_date": frappe.db.get_value("Opportunity", self.name, "contact_date") if \
				(not cint(self.get("__islocal"))) else None,
			"contact_by": frappe.db.get_value("Opportunity", self.name, "contact_by") if \
				(not cint(self.get("__islocal"))) else None,
		})

		self.make_new_lead_if_required()

		if not self.enquiry_from:
			frappe.throw(_("Opportunity From field is mandatory"))

		self.set_status()
		self.validate_item_details()
		self.validate_uom_is_integer("uom", "qty")
		self.validate_lead_cust()
		self.validate_cust_name()

		if not self.title:
			self.title = self.customer_name

		from erpnext.accounts.utils import validate_fiscal_year
		validate_fiscal_year(self.transaction_date, self.fiscal_year, _("Opportunity Date"), self)

	def make_new_lead_if_required(self):
		"""Set lead against new opportunity"""
		if not self.lead or self.customer:
			lead_name = frappe.db.get_value("Lead", {"email_id": self.contact_email})
			if not lead_name:
				lead = frappe.get_doc({
					"doctype": "Lead",
					"email_id": self.contact_email,
					"lead_name": self.contact_email
				})
				lead.insert(ignore_permissions=True)
				lead_name = lead.name

			self.enquiry_from = "Lead"
			self.lead = lead_name

	def declare_enquiry_lost(self,arg):
		if not self.has_quotation():
			frappe.db.set(self, 'status', 'Lost')
			frappe.db.set(self, 'order_lost_reason', arg)
		else:
			frappe.throw(_("Cannot declare as lost, because Quotation has been made."))

	def on_trash(self):
		self.delete_events()

	def has_quotation(self):
		return frappe.db.get_value("Quotation Item", {"prevdoc_docname": self.name, "docstatus": 1})

	def has_ordered_quotation(self):
		return frappe.db.sql("""select q.name from `tabQuotation` q, `tabQuotation Item` qi
			where q.name = qi.parent and q.docstatus=1 and qi.prevdoc_docname =%s and q.status = 'Ordered'""", self.name)

	def validate_cust_name(self):
		if self.customer:
			self.customer_name = frappe.db.get_value("Customer", self.customer, "customer_name")
		elif self.lead:
			self.customer_name = frappe.db.get_value("Lead", self.lead, "lead_name")

	def get_cust_address(self,name):
		details = frappe.db.sql("""select customer_name, address, territory, customer_group
			from `tabCustomer` where name = %s and docstatus != 2""", (name), as_dict = 1)
		if details:
			ret = {
				'customer_name':	details and details[0]['customer_name'] or '',
				'address'	:	details and details[0]['address'] or '',
				'territory'			 :	details and details[0]['territory'] or '',
				'customer_group'		:	details and details[0]['customer_group'] or ''
			}
			# ********** get primary contact details (this is done separately coz. , in case there is no primary contact thn it would not be able to fetch customer details in case of join query)

			contact_det = frappe.db.sql("""select contact_name, contact_no, email_id
				from `tabContact` where customer = %s and is_customer = 1
					and is_primary_contact = 'Yes' and docstatus != 2""", name, as_dict = 1)

			ret['contact_person'] = contact_det and contact_det[0]['contact_name'] or ''
			ret['contact_no']		 = contact_det and contact_det[0]['contact_no'] or ''
			ret['email_id']			 = contact_det and contact_det[0]['email_id'] or ''

			return ret
		else:
			frappe.throw(_("Customer {0} does not exist").format(name), frappe.DoesNotExistError)

	def on_update(self):
		self.add_calendar_event()

	def add_calendar_event(self, opts=None, force=False):
		if not opts:
			opts = frappe._dict()

		opts.description = ""
		opts.contact_date = self.contact_date

		if self.customer:
			if self.contact_person:
				opts.description = 'Contact '+cstr(self.contact_person)
			else:
				opts.description = 'Contact customer '+cstr(self.customer)
		elif self.lead:
			if self.contact_display:
				opts.description = 'Contact '+cstr(self.contact_display)
			else:
				opts.description = 'Contact lead '+cstr(self.lead)

		opts.subject = opts.description
		opts.description += '. By : ' + cstr(self.contact_by)

		if self.to_discuss:
			opts.description += ' To Discuss : ' + cstr(self.to_discuss)

		super(Opportunity, self).add_calendar_event(opts, force)

	def validate_item_details(self):
		if not self.get('items'):
			return

		# set missing values
		item_fields = ("item_name", "description", "item_group", "brand")

		for d in self.items:
			if not d.item_code:
				continue

			item = frappe.db.get_value("Item", d.item_code, item_fields, as_dict=True)
			for key in item_fields:
				if not d.get(key): d.set(key, item.get(key))

	def validate_lead_cust(self):
		if self.enquiry_from == 'Lead':
			if not self.lead:
				frappe.throw(_("Lead must be set if Opportunity is made from Lead"))
			else:
				self.customer = None
		elif self.enquiry_from == 'Customer':
			if not self.customer:
				msgprint("Customer is mandatory if 'Opportunity From' is selected as Customer", raise_exception=1)
			else:
				self.lead = None

@frappe.whitelist()
def get_item_details(item_code):
	item = frappe.db.sql("""select item_name, stock_uom, image, description, item_group, brand
		from `tabItem` where name = %s""", item_code, as_dict=1)
	return {
		'item_name': item and item[0]['item_name'] or '',
		'uom': item and item[0]['stock_uom'] or '',
		'description': item and item[0]['description'] or '',
		'image': item and item[0]['image'] or '',
		'item_group': item and item[0]['item_group'] or '',
		'brand': item and item[0]['brand'] or ''
	}

@frappe.whitelist()
def make_quotation(source_name, target_doc=None):
	def set_missing_values(source, target):
		quotation = frappe.get_doc(target)
		quotation.currency = None # set it as default from customer
		quotation.run_method("set_missing_values")
		quotation.run_method("calculate_taxes_and_totals")

	doclist = get_mapped_doc("Opportunity", source_name, {
		"Opportunity": {
			"doctype": "Quotation",
			"field_map": {
				"enquiry_from": "quotation_to",
				"enquiry_type": "order_type",
				"name": "enq_no",
			}
		},
		"Opportunity Item": {
			"doctype": "Quotation Item",
			"field_map": {
				"parent": "prevdoc_docname",
				"parenttype": "prevdoc_doctype",
				"uom": "stock_uom"
			},
			"add_if_empty": True
		}
	}, target_doc, set_missing_values)

	return doclist

@frappe.whitelist()
def set_multiple_status(names, status):
	names = json.loads(names)
	for name in names:
		opp = frappe.get_doc("Opportunity", name)
		opp.status = status
		opp.save()
