# -*- coding: UTF-8 -*-
# Copyright 2016 Luc Saffre
# This file is part of Lino Cosi.
#
# Lino Cosi is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# Lino Cosi is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with Lino Cosi.  If not, see
# <http://www.gnu.org/licenses/>.

"""
Database models for `lino_cosi.lib.invoicing`.

"""

from __future__ import unicode_literals

from decimal import Decimal
ZERO = Decimal()

from django.db import models

from django.utils.translation import string_concat
from django.utils import translation

# from lino.utils.xmlgen.html import E, join_elems
from lino.modlib.gfks.fields import GenericForeignKeyIdField
from lino.core.gfks import GenericForeignKey, ContentType

from lino.modlib.users.mixins import UserAuthored, My

# from lino_cosi.lib.ledger.choicelists import VoucherTypes

from lino.api import dd, rt, _
from lino_cosi.lib.ledger.roles import LedgerStaff
from .mixins import Invoiceable
from .actions import (UpdatePlan, ExecutePlan, ToggleSelection,
                      StartInvoicing, StartInvoicingForJournal,
                      StartInvoicingForPartner)


@dd.python_2_unicode_compatible
class Plan(UserAuthored):
    """An **invoicing plan** is a rather temporary database object which
    represents the plan of a given user to have Lino generate a series
    of invoices.

    .. attribute:: user
    .. attribute:: journal
    .. attribute:: max_date
    .. attribute:: today
    .. attribute:: partner

    .. attribute:: update_plan
    .. attribute:: execute_plan

    """
    class Meta:
        app_label = 'invoicing'
        verbose_name = _("Invoicing plan")
        verbose_name_plural = _("Invoicing plans")

    journal = models.ForeignKey('ledger.Journal', blank=True, null=True)
    max_date = models.DateField(
        _("Invoiceables until"), default=dd.today)
    today = models.DateField(
        _("Invoicing date"), default=dd.today)
    partner = models.ForeignKey('contacts.Partner', blank=True, null=True)

    update_plan = UpdatePlan()
    execute_plan = ExecutePlan()
    start_invoicing = StartInvoicing()

    @dd.chooser()
    def journal_choices(cls):
        vt = dd.plugins.invoicing.get_voucher_type()
        return rt.modules.ledger.Journal.objects.filter(voucher_type=vt)

    def get_invoiceables_for_plan(self, partner=None):
        for m in rt.models_by_base(Invoiceable):
            for obj in m.get_invoiceables_for_plan(self, partner):
                if obj.get_invoiceable_product() is not None:
                    yield obj

    @classmethod
    def start_plan(cls, user, **options):
        """Start an invoicing plan for the given user on the database object
        defined by `k` and `v`. Where `k` is the name of the field
        used to select the plan (e.g. `'partner'` or `'journal'`) and
        `v` is the value for that field.

        This will either create a new plan, or check whether the
        currently existing plan for this user was for the same
        database object. If it was for another object, then clear all
        items.

        """
        try:
            plan = cls.objects.get(user=user)
            changed = False
            for k, v in options.items():
                if getattr(plan, k) != v:
                    changed = True
                    setattr(plan, k, v)
            if changed:
                plan.items.all().delete()
        except cls.DoesNotExist:
            plan = cls(user=user, **options)
        plan.save()
        return plan

    def fill_plan(self, ar):
        """Yield a list of invoiceables for the given partner,
        one for each invoice line to generate.


        """
        Item = rt.modules.invoicing.Item
        collected = dict()
        for obj in self.get_invoiceables_for_plan():
            partner = obj.get_invoiceable_partner()
            idate = obj.get_invoiceable_date()
            item = collected.get(partner, None)
            if item is None:
                item = Item(plan=self, partner=partner)
                collected[partner] = item
            if item.first_date is None:
                item.first_date = idate
            else:
                item.first_date = min(idate, item.first_date)
            if item.last_date is None:
                item.last_date = idate
            else:
                item.last_date = max(idate, item.last_date)
            if obj.amount:
                item.amount += obj.amount
            item.number_of_invoiceables += 1
            item.save()

    # def execute_plan(self,  ar):
    #     """Create an invoice for the given partner.
    #     """
    #     InvoiceItem = rt.modules.sales.InvoiceItem

    # @dd.displayfield(_("Actions"))
    # def action_buttons(self, ar):
    #     if ar is None:
    #         return ''
    #     elems = []
    #     elems.append(ar.instance_action_button(self.toggle_selections))
    #     elems = join_elems(*elems, sep=", ")
    #     return E.p(*elems)
    #     # return obj.partner.show_invoiceables.as_button(ar)
    #     # return obj.partner.create_invoice.as_button(ar)

    toggle_selections = ToggleSelection()

    def __str__(self):
        # return "{0} {1}".format(self._meta.verbose_name, self.user)
        # return self._meta.verbose_name
        return unicode(self.user)


class Item(dd.Model):
    """The items of an invoicing plan are called **suggestions**.
    """
    class Meta:
        app_label = 'invoicing'
        verbose_name = _("Invoicing suggestion")
        verbose_name_plural = _("Invoicing suggestions")

    plan = models.ForeignKey('invoicing.Plan', related_name="items")
    partner = models.ForeignKey('contacts.Partner')
    first_date = models.DateField(_("First date"))
    last_date = models.DateField(_("Last date"))
    amount = dd.PriceField(_("Amount"), default=ZERO)
    number_of_invoiceables = models.IntegerField(_("Number"), default=0)
    selected = models.BooleanField(_("Selected"), default=True)
    invoice = models.ForeignKey(
        dd.plugins.invoicing.voucher_model, null=True, blank=True,
        on_delete=models.SET_NULL)

    def create_invoice(self,  ar):
        """Create the invoice corresponding to this item of the plan.
        """
        ITEM_MODEL = dd.resolve_model(dd.plugins.invoicing.item_model)
        VOUCHER_MODEL = ITEM_MODEL._meta.get_field('voucher').rel.to
        # M = rt.modules.sales.VatProductInvoice
        M = VOUCHER_MODEL
        invoice = M(partner=self.partner, journal=self.plan.journal,
                    voucher_date=self.plan.today,
                    entry_date=self.plan.today)
        lng = invoice.get_print_language()
        items = []
        with translation.override(lng):
            for ii in self.plan.get_invoiceables_for_plan(self.partner):
                for i in ii.get_wanted_items(ar, invoice, ITEM_MODEL):
                    items.append(i)

        if len(items) == 0:
            ar.info(_("No invoiceables found for %s.") % self)
            return

        invoice.full_clean()
        invoice.save()

        for i in items:
            i.voucher = invoice
            i.full_clean()
            i.save()

        self.invoice = invoice
        self.save()

        invoice.compute_totals()
        invoice.full_clean()
        invoice.save()

        return invoice


class Plans(dd.Table):
    model = "invoicing.Plan"
    detail_layout = """user journal max_date today partner
    invoicing.ItemsByPlan
    """


class MyPlans(My, Plans):
    required_roles = dd.login_required(LedgerStaff)


class AllPlans(Plans):
    required_roles = dd.login_required(LedgerStaff)


class Items(dd.Table):
    model = "invoicing.Item"


class ItemsByPlan(Items):
    verbose_name_plural = _("Suggestions")
    master_key = 'plan'
    column_names = "selected partner number_of_invoiceables amount invoice *"


class InvoicingsByInvoiceable(dd.Table):
    model = dd.plugins.invoicing.item_model
    label = _("Invoicings")
    master_key = 'invoiceable'
    editable = False
    column_names = "voucher qty title description:20x1 #discount " \
                   "unit_price total_incl #total_base #total_vat *"


invoiceable_label = dd.plugins.invoicing.invoiceable_label

dd.inject_field(
    dd.plugins.invoicing.item_model,
    'invoiceable_type', dd.ForeignKey(
        ContentType,
        editable=False, blank=True, null=True,
        verbose_name=string_concat(invoiceable_label, ' ', _('(type)'))))
dd.inject_field(
    dd.plugins.invoicing.item_model,
    'invoiceable_id', GenericForeignKeyIdField(
        'invoiceable_type',
        editable=False, blank=True, null=True,
        verbose_name=string_concat(invoiceable_label, ' ', _('(object)'))))
dd.inject_field(
    dd.plugins.invoicing.item_model,
    'invoiceable', GenericForeignKey(
        'invoiceable_type', 'invoiceable_id',
        verbose_name=invoiceable_label))


@dd.receiver(dd.pre_save, sender=dd.plugins.invoicing.item_model)
def item_pre_save_handler(sender=None, instance=None, **kwargs):
    """When the user sets `title` of an automatically generated invoice
    item to an empty string, then Lino restores the default value for
    both title and description

    """
    self = instance
    if self.invoiceable_id and not self.title:
        lng = self.voucher.get_print_language()
        # lng = self.voucher.partner.language or dd.get_default_language()
        with translation.override(lng):
            self.title = self.invoiceable.get_invoiceable_title(self.voucher)
            self.invoiceable.setup_invoice_item(self)


# def get_invoicing_voucher_type():
#     voucher_model = dd.resolve_model(dd.plugins.invoicing.voucher_model)
#     vt = VoucherTypes.get_for_model(voucher_model)


@dd.receiver(dd.pre_analyze)
def install_start_action(sender=None, **kwargs):
    vt = dd.plugins.invoicing.get_voucher_type()
    # vt = get_invoicing_voucher_type()
    vt.table_class.start_invoicing = StartInvoicingForJournal()

    rt.modules.contacts.Partner.start_invoicing = StartInvoicingForPartner()
    
