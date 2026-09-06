import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from openpyxl.utils.datetime import to_excel

from admin_panel import views
from cashier.models import Cheque, FundCluster, Report, Supplier


class RegistrySheetTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_superuser(username='admin', email='admin@example.com', password='pass12345')
		self.supplier = Supplier.objects.create(
			account_name='Original Name',
			account_number='001',
			address='Old Address',
			tin='TIN-1',
			status='active',
			created_by=self.admin,
			raw_import={
				'sheet': 'Registry',
				'row_number': 1,
				'headers': ['account_name', 'account_number', 'address', 'tin', 'status'],
				'columns': ['account_name', 'account_number', 'address', 'tin', 'status'],
				'values': ['Original Name', '001', 'Old Address', 'TIN-1', 'active'],
				'cells': [
					{'header': 'account_name', 'value': 'Original Name'},
					{'header': 'account_number', 'value': '001'},
					{'header': 'address', 'value': 'Old Address'},
					{'header': 'tin', 'value': 'TIN-1'},
					{'header': 'status', 'value': 'active'},
				],
				'data': {
					'account_name': 'Original Name',
					'account_number': '001',
					'address': 'Old Address',
					'tin': 'TIN-1',
					'status': 'active',
				},
			},
		)
		self.cashier = User.objects.create_user(username='cashier', email='cashier@example.com', password='pass12345')

	def test_registry_sheet_save_updates_supplier(self):
		self.client.force_login(self.admin)

		payload = {
			'sheet_name': 'Registry',
			'headers': ['account_name', 'account_number', 'address', 'tin', 'status'],
			'rows': [
				{
					'supplier_id': self.supplier.pk,
					'cells': ['Updated Name', '999', 'New Address', 'TIN-9', 'inactive'],
				}
			],
		}

		response = self.client.post(
			reverse('registry'),
			data={
				'action': 'save',
				'sheet_payload': json.dumps(payload),
			},
		)

		self.assertEqual(response.status_code, 200)
		self.supplier.refresh_from_db()
		self.assertEqual(self.supplier.account_name, 'Updated Name')
		self.assertEqual(self.supplier.account_number, '999')
		self.assertEqual(self.supplier.address, 'New Address')
		self.assertEqual(self.supplier.tin, 'TIN-9')
		self.assertEqual(self.supplier.status, 'inactive')
		self.assertEqual(self.supplier.raw_import['cells'][0]['value'], 'Updated Name')

	def test_cashier_supplier_defaults_follow_own_flow(self):
		Supplier.objects.create(
			account_name='Admin Flow Supplier',
			created_by=self.admin,
			series_month='99',
			series_day='12',
		)

		self.client.force_login(self.cashier)
		response = self.client.get(reverse('suppliers'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['auto_supplier_series_month'], '1')
		self.assertEqual(response.context['auto_supplier_series_day'], '1')

	def test_cashier_or_number_uses_highest_existing_value(self):
		Supplier.objects.create(
			account_name='Older OR Supplier',
			created_by=self.cashier,
			or_number='1-32319',
		)
		Supplier.objects.create(
			account_name='Newer OR Supplier',
			created_by=self.cashier,
			or_number='1-54127',
		)
		Supplier.objects.create(
			account_name='Admin OR Supplier',
			created_by=self.admin,
			or_number='1-99999',
		)

		self.client.force_login(self.cashier)
		response = self.client.get(reverse('suppliers'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['auto_supplier_defaults']['or_number'], '1-54128')

	def test_past_dated_supplier_is_labeled_old_or(self):
		old_supplier = Supplier.objects.create(
			account_name='Old Date Supplier',
			created_by=self.cashier,
			mr='2016-jun-30',
			mr_or='2016-jun-30',
			raw_import={
				'data': {
					'date': '2016-06-30',
					'mr': '2016-jun-30',
				},
			},
			or_number='1-10001',
		)
		current_supplier = Supplier.objects.create(
			account_name='Current Date Supplier',
			created_by=self.cashier,
			date=date.today(),
			or_number='1-10002',
		)

		self.client.force_login(self.cashier)
		response = self.client.get(reverse('suppliers'))

		self.assertEqual(response.status_code, 200)
		page_suppliers = list(response.context['page_obj'].object_list)
		self.assertTrue(any(s.pk == old_supplier.pk and s.is_old_or for s in page_suppliers))
		self.assertTrue(any(s.pk == current_supplier.pk and not s.is_old_or for s in page_suppliers))

	def test_supplier_defaults_use_current_flow_values(self):
		current_supplier = Supplier.objects.create(
			account_name='Current Flow Supplier',
			created_by=self.cashier,
			date=date.today(),
			mr_or='2026-may-31',
			mr='2026-may-31',
			code_line='NEW-LINE',
			code_noc='NEW-NOC',
			nature_of_collections='New Nature',
			raw_import={
				'data': {
					'date': date.today().isoformat(),
					'mr': '2026-may-31',
				},
			},
		)
		old_supplier = Supplier.objects.create(
			account_name='Old Flow Supplier',
			created_by=self.cashier,
			mr_or='2016-jun-30',
			mr='2016-jun-30',
			code_line='OLD-LINE',
			code_noc='OLD-NOC',
			nature_of_collections='Old Nature',
			raw_import={
				'data': {
					'date': '2016-06-30',
					'mr': '2016-jun-30',
				},
			},
		)
		Supplier.objects.filter(pk=old_supplier.pk).update(created_at=timezone.now())

		self.client.force_login(self.cashier)
		response = self.client.get(reverse('suppliers'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['auto_supplier_defaults']['mr_or'], '2026-may-32')
		self.assertEqual(response.context['auto_supplier_defaults']['code_line'], 'NEW-LINE-1')
		self.assertEqual(response.context['auto_supplier_defaults']['code_noc'], 'NEW-NOC-1')

	def test_supplier_import_creates_new_rows_for_duplicate_payee(self):
		rows = [
			{
				'PAYEE': 'Shared Payee',
				'OR NUMBER': '1-10001',
				'MR OR': '2026-may-31',
				'MR': '2026-may-31',
				'CODE OR': '1-1',
				'CODE OR 2': '1-1-1',
				'SERIES/MONTH': '1',
				'DATE': '2026-05-31',
				'SERIES/DAY': '1',
				'CODE LINE': 'LINE-1',
				'LINE': '1',
				'CODE NOC': 'NOC-1',
				'NATURE OF COLLECTIONS': 'Nature 1',
				'AMOUNT': '100',
				'REMARKS': 'First',
			},
			{
				'PAYEE': 'Shared Payee',
				'OR NUMBER': '1-10002',
				'MR OR': '2026-may-31',
				'MR': '2026-may-31',
				'CODE OR': '1-1',
				'CODE OR 2': '1-1-2',
				'SERIES/MONTH': '1',
				'DATE': '2026-05-31',
				'SERIES/DAY': '2',
				'CODE LINE': 'LINE-2',
				'LINE': '2',
				'CODE NOC': 'NOC-2',
				'NATURE OF COLLECTIONS': 'Nature 2',
				'AMOUNT': '200',
				'REMARKS': 'Second',
			},
		]

		count, _ = views._do_import('suppliers', rows, self.cashier)

		self.assertEqual(count, 2)
		self.assertEqual(Supplier.objects.filter(account_name='Shared Payee').count(), 2)

	def test_supplier_import_blocks_exact_duplicate_in_system(self):
		Supplier.objects.create(
			account_name='Blocked Payee',
			created_by=self.cashier,
			account_number='1-80001',
			mr_or='2026-may-31',
			mr='2026-may-31',
			date=date(2026, 5, 31),
			or_number='1-80001',
			code_line='LINE-Z',
			code_noc='NOC-Z',
		)
		rows = [
			{
				'PAYEE': 'Blocked Payee',
				'OR NUMBER': '1-80001',
				'MR OR': '2026-may-31',
				'MR': '2026-may-31',
				'CODE OR': '1-8',
				'CODE OR 2': '1-8-1',
				'SERIES/MONTH': '8',
				'DATE': '2026-05-31',
				'SERIES/DAY': '1',
				'CODE LINE': 'LINE-Z2',
				'LINE': 'Z2',
				'CODE NOC': 'NOC-Z2',
				'NATURE OF COLLECTIONS': 'Nature Z2',
				'AMOUNT': '500',
				'REMARKS': 'Duplicate row',
			},
		]

		count, _ = views._do_import('suppliers', rows, self.cashier)

		self.assertEqual(count, 0)
		self.assertEqual(Supplier.objects.filter(account_name='Blocked Payee').count(), 1)
		supplier = Supplier.objects.get(account_name='Blocked Payee')
		self.assertEqual(supplier.code_line, 'LINE-Z')
		self.assertEqual(supplier.code_noc, 'NOC-Z')
		self.assertEqual(supplier.amount, Decimal('0.00'))

	def test_supplier_import_merges_only_when_all_keys_match(self):
		rows = [
			{
				'PAYEE': 'Merge Payee',
				'OR NUMBER': '1-90001',
				'MR OR': '2026-may-31',
				'MR': '2026-may-31',
				'CODE OR': '1-9',
				'CODE OR 2': '1-9-1',
				'SERIES/MONTH': '9',
				'DATE': '2026-05-31',
				'SERIES/DAY': '1',
				'CODE LINE': 'LINE-A',
				'LINE': 'A',
				'CODE NOC': 'NOC-A',
				'NATURE OF COLLECTIONS': 'Nature A',
				'AMOUNT': '100',
				'REMARKS': 'First',
			},
			{
				'PAYEE': 'Merge Payee',
				'OR NUMBER': '1-90001',
				'MR OR': '2026-may-31',
				'MR': '2026-may-31',
				'CODE OR': '1-9',
				'CODE OR 2': '1-9-1',
				'SERIES/MONTH': '9',
				'DATE': '2026-05-31',
				'SERIES/DAY': '1',
				'CODE LINE': 'LINE-B',
				'LINE': 'B',
				'CODE NOC': 'NOC-B',
				'NATURE OF COLLECTIONS': 'Nature B',
				'AMOUNT': '250',
				'REMARKS': 'Second',
			},
		]

		count, _ = views._do_import('suppliers', rows, self.cashier)

		self.assertEqual(count, 1)
		self.assertEqual(Supplier.objects.filter(account_name='Merge Payee').count(), 1)
		supplier = Supplier.objects.get(account_name='Merge Payee')
		self.assertEqual(supplier.code_line, 'LINE-A')
		self.assertEqual(supplier.code_noc, 'NOC-A')
		self.assertEqual(supplier.amount, Decimal('100.00'))

	def test_supplier_import_parses_non_iso_dates(self):
		rows = [
			{
				'PAYEE': 'Date Format Supplier',
				'OR NUMBER': '1-20001',
				'MR OR': '2026-may-31',
				'MR': '2026-may-31',
				'CODE OR': '1-2',
				'CODE OR 2': '1-2-1',
				'SERIES/MONTH': '2',
				'DATE': 'May 29, 2026',
				'SERIES/DAY': '1',
				'CODE LINE': 'LINE-3',
				'LINE': '3',
				'CODE NOC': 'NOC-3',
				'NATURE OF COLLECTIONS': 'Nature 3',
				'AMOUNT': '300',
				'REMARKS': 'Date test',
			},
		]

		views._do_import('suppliers', rows, self.cashier)

		supplier = Supplier.objects.get(account_name='Date Format Supplier')
		self.assertEqual(supplier.date, date(2026, 5, 29))

	def test_supplier_import_parses_excel_serial_dates(self):
		rows = [
			{
				'PAYEE': 'Serial Date Supplier',
				'OR NUMBER': '1-30001',
				'MR OR': '2026-may-31',
				'MR': '2026-may-31',
				'CODE OR': '1-3',
				'CODE OR 2': '1-3-1',
				'SERIES/MONTH': '3',
				'DATE': to_excel(date(2026, 5, 29)),
				'SERIES/DAY': '1',
				'CODE LINE': 'LINE-4',
				'LINE': '4',
				'CODE NOC': 'NOC-4',
				'NATURE OF COLLECTIONS': 'Nature 4',
				'AMOUNT': '400',
				'REMARKS': 'Serial date test',
			},
		]

		views._do_import('suppliers', rows, self.cashier)

		supplier = Supplier.objects.get(account_name='Serial Date Supplier')
		self.assertEqual(supplier.date, date(2026, 5, 29))

		self.client.force_login(self.cashier)
		response = self.client.get(reverse('suppliers'))
		self.assertContains(response, 'May 29, 2026')

	def test_supplier_table_uses_mr_when_date_is_blank(self):
		Supplier.objects.create(
			account_name='MR Date Supplier',
			created_by=self.cashier,
			mr='2026-MAY-19',
			mr_or='2026-MAY-19',
			date=None,
			or_number='1-40001',
		)

		self.client.force_login(self.cashier)
		response = self.client.get(reverse('suppliers'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'May 19, 2026')

	def test_cheque_summary_snapshot_filters_selected_fund_cluster(self):
		fund_one = FundCluster.objects.create(
			code='FC-001',
			name='Fund One',
			bank_name='Bank A',
			account_number='111-222',
		)
		fund_two = FundCluster.objects.create(
			code='FC-002',
			name='Fund Two',
			bank_name='Bank B',
			account_number='333-444',
		)
		supplier = Supplier.objects.create(account_name='Cheque Supplier', created_by=self.cashier)
		Cheque.objects.create(
			cheque_number='000001',
			payee=supplier,
			payee_name='Cheque Supplier',
			fund_cluster=fund_one,
			amount=Decimal('100.00'),
			date=date(2026, 5, 10),
			status='released',
			created_by=self.admin,
		)
		Cheque.objects.create(
			cheque_number='000002',
			payee=supplier,
			payee_name='Cheque Supplier',
			fund_cluster=fund_two,
			amount=Decimal('200.00'),
			date=date(2026, 5, 11),
			status='released',
			created_by=self.admin,
		)

		snapshot = views._build_report_snapshot('cheque_summary', '2026-05-01', '2026-05-31', fund_cluster_id=fund_one.pk)

		self.assertEqual(snapshot['fund_cluster_id'], fund_one.pk)
		self.assertEqual(snapshot['fund_cluster'], str(fund_one))
		self.assertEqual(snapshot['bank_account'], 'Bank A / 111-222')
		self.assertEqual(snapshot['cheque_count'], 1)
		self.assertEqual(len(snapshot['rows']), 1)

	def test_generate_report_uses_selected_preview_rows(self):
		cheque_one = Cheque.objects.create(
			cheque_number='000101',
			payee=self.supplier,
			payee_name=self.supplier.account_name,
			amount=Decimal('150.00'),
			date=date(2026, 5, 1),
			status='released',
			created_by=self.admin,
		)
		Cheque.objects.create(
			cheque_number='000102',
			payee=self.supplier,
			payee_name=self.supplier.account_name,
			amount=Decimal('175.00'),
			date=date(2026, 5, 2),
			status='released',
			created_by=self.admin,
		)

		self.client.force_login(self.admin)
		response = self.client.post(
			reverse('reports'),
			data={
				'report_type': 'cheque_summary',
				'date_from': '2026-05-01',
				'date_to': '2026-05-31',
				'selected_rows': str(cheque_one.pk),
			},
		)

		self.assertEqual(response.status_code, 302)
		report = Report.objects.latest('generated_at')
		self.assertEqual(report.report_type, 'cheque_summary')
		self.assertEqual(len(report.data_snapshot['rows']), 1)
		self.assertEqual(report.data_snapshot['rows'][0]['id'], cheque_one.pk)

	def test_edit_report_previews_live_rows_and_updates_same_record(self):
		first_cheque = Cheque.objects.create(
			cheque_number='000201',
			payee=self.supplier,
			payee_name=self.supplier.account_name,
			amount=Decimal('210.00'),
			date=date(2026, 5, 3),
			status='released',
			created_by=self.admin,
		)
		second_cheque = Cheque.objects.create(
			cheque_number='000202',
			payee=self.supplier,
			payee_name=self.supplier.account_name,
			amount=Decimal('220.00'),
			date=date(2026, 5, 4),
			status='released',
			created_by=self.admin,
		)

		self.client.force_login(self.admin)
		create_response = self.client.post(
			reverse('reports'),
			data={
				'report_type': 'cheque_summary',
				'date_from': '2026-05-01',
				'date_to': '2026-05-31',
				'selected_rows': str(first_cheque.pk),
			},
		)
		self.assertEqual(create_response.status_code, 302)
		report = Report.objects.latest('generated_at')
		original_report_id = report.pk

		preview_response = self.client.get(
			reverse('report_export_preview'),
			data={
				'report_type': 'cheque_summary',
				'edit_report_id': str(report.pk),
			},
		)
		self.assertEqual(preview_response.status_code, 200)
		preview_data = preview_response.json()
		self.assertEqual(preview_data['row_count'], 2)
		self.assertEqual(preview_data['selected_row_ids'], [str(first_cheque.pk)])

		update_response = self.client.post(
			reverse('reports'),
			data={
				'report_id': str(report.pk),
				'selected_rows': str(second_cheque.pk),
			},
		)
		self.assertEqual(update_response.status_code, 302)
		report.refresh_from_db()
		self.assertEqual(report.pk, original_report_id)
		self.assertEqual(len(report.data_snapshot['rows']), 1)
		self.assertEqual(report.data_snapshot['rows'][0]['id'], second_cheque.pk)


class SupplierGrossCalculationTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_superuser(username='admin', email='admin@example.com', password='pass12345')
		from admin_panel.models import ManagementOption
		ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX).delete()
		ManagementOption.objects.create(category=ManagementOption.CATEGORY_TAX, value='Tax (5%/3%)', uacs='5%/3%', is_active=True)
		ManagementOption.objects.create(category=ManagementOption.CATEGORY_TAX, value='Tax (2%/1%)', uacs='2%/1%', is_active=True)

	def test_supplier_creation_gross_calculation_vat(self):
		self.client.force_login(self.admin)
		response = self.client.post(
			reverse('suppliers'),
			data={
				'action': 'create',
				'payee': 'VAT Supplier Test',
				'amount': '8640.00',
				'is_vat': 'on',
				'professional_tax_rate': '3.00',
				'other_deductions': '150.00',
			}
		)
		self.assertEqual(response.status_code, 200)
		self.assertIn("added", response.context.get('success', ''))
		supplier = Supplier.objects.get(account_name='VAT Supplier Test')
		extras = supplier.raw_import.get('supplier_extras') or {}
		self.assertEqual(extras.get('tax_5'), '430.88')
		self.assertEqual(extras.get('tax_2'), '172.35')
		self.assertEqual(extras.get('professional_tax'), '258.53')
		self.assertEqual(extras.get('gross_amount'), '9651.76')

	def test_supplier_creation_gross_calculation_non_vat(self):
		self.client.force_login(self.admin)
		response = self.client.post(
			reverse('suppliers'),
			data={
				'action': 'create',
				'payee': 'Non-VAT Supplier Test',
				'amount': '8640.00',
				'is_vat': '',
				'professional_tax_rate': '3.00',
				'other_deductions': '150.00',
			}
		)
		self.assertEqual(response.status_code, 200)
		self.assertIn("added", response.context.get('success', ''))
		supplier = Supplier.objects.get(account_name='Non-VAT Supplier Test')
		extras = supplier.raw_import.get('supplier_extras') or {}
		self.assertEqual(extras.get('tax_5'), '0.00')
		self.assertEqual(extras.get('tax_2'), '0.00')
		self.assertEqual(extras.get('professional_tax'), '271.86')
		self.assertEqual(extras.get('gross_amount'), '9061.86')

	def test_supplier_creation_with_percent_sign_in_rate(self):
		self.client.force_login(self.admin)
		response = self.client.post(
			reverse('suppliers'),
			data={
				'action': 'create',
				'payee': 'Percent Sign Supplier Test',
				'amount': '8640.00',
				'is_vat': '',
				'professional_tax_rate': '3.00%',
				'other_deductions': '150.00',
			}
		)
		self.assertEqual(response.status_code, 200)
		self.assertIn("added", response.context.get('success', ''))
		supplier = Supplier.objects.get(account_name='Percent Sign Supplier Test')
		extras = supplier.raw_import.get('supplier_extras') or {}
		self.assertEqual(extras.get('tax_5'), '0.00')
		self.assertEqual(extras.get('tax_2'), '0.00')
		self.assertEqual(extras.get('professional_tax'), '271.86')
		self.assertEqual(extras.get('gross_amount'), '9061.86')

	def test_supplier_import_auto_vat_detection(self):
		from cashier.models import FundCluster
		FundCluster.objects.get_or_create(code='164', defaults={'name': 'General Fund'})

		rows = [
			{
				'PAYEE': 'Imported Non-VAT Supplier',
				'OR NUMBER': '1-90005',
				'AMOUNT': '1000.00',
				'PROFESSIONAL TAX': '30.00',
				'TAX 5%': '0.00',
				'TAX 2%': '0.00',
				'FUND CLUSTER': '164',
			},
			{
				'PAYEE': 'Imported VAT Supplier',
				'OR NUMBER': '1-90006',
				'AMOUNT': '1000.00',
				'PROFESSIONAL TAX': '30.00',
				'TAX 5%': '50.00',
				'TAX 2%': '10.00',
				'FUND CLUSTER': '164',
			}
		]
		from admin_panel import views
		count, warnings = views._do_import('suppliers', rows, self.admin)
		self.assertEqual(count, 2)

		supplier_nv = Supplier.objects.get(account_name='Imported Non-VAT Supplier')
		extras_nv = supplier_nv.raw_import.get('supplier_extras') or {}
		self.assertEqual(extras_nv.get('is_vat'), 'false')

		supplier_v = Supplier.objects.get(account_name='Imported VAT Supplier')
		extras_v = supplier_v.raw_import.get('supplier_extras') or {}
		self.assertEqual(extras_v.get('is_vat'), 'true')

	def test_supplier_import_other_deductions(self):
		from cashier.models import FundCluster
		FundCluster.objects.get_or_create(code='164', defaults={'name': 'General Fund'})

		rows = [
			{
				'PAYEE': 'Imported Supplier with Deductions',
				'OR NUMBER': '1-90007',
				'AMOUNT': '1000.00',
				'OTHER DEDUCTIONS': '150.00',
				'FUND CLUSTER': '164',
			}
		]
		from admin_panel import views
		count, warnings = views._do_import('suppliers', rows, self.admin)
		self.assertEqual(count, 1)

		supplier = Supplier.objects.get(account_name='Imported Supplier with Deductions')
		extras = supplier.raw_import.get('supplier_extras') or {}
		self.assertEqual(extras.get('other_deductions'), '150.00')

	def test_supplier_import_auto_account_title_by_uacs(self):
		from admin_panel.models import ManagementOption, AccountTitleGroup
		from cashier.models import FundCluster
		FundCluster.objects.get_or_create(code='164', defaults={'name': 'General Fund'})

		# Setup account title options & groups
		ManagementOption.objects.filter(category=ManagementOption.CATEGORY_ACCOUNT_TITLE).delete()
		AccountTitleGroup.objects.all().delete()

		option = ManagementOption.objects.create(
			category=ManagementOption.CATEGORY_ACCOUNT_TITLE,
			value='Traveling Expenses - Local',
			uacs='5020603000',
			is_active=True
		)

		group = AccountTitleGroup.objects.create(
			name='Training Expenses',
			uacs='5020201000'
		)

		rows = [
			{
				'PAYEE': 'UACS Option Supplier',
				'OR NUMBER': '1-90008',
				'AMOUNT': '1000.00',
				'UACS Object Code': '5020603000.0',
				'FUND CLUSTER': '164',
			},
			{
				'PAYEE': 'UACS Group Supplier',
				'OR NUMBER': '1-90009',
				'AMOUNT': '2000.00',
				'UACS Object Code': '5020201000',
				'FUND CLUSTER': '164',
			}
		]

		from admin_panel import views
		count, warnings = views._do_import('suppliers', rows, self.admin)
		self.assertEqual(count, 2)

		supplier_opt = Supplier.objects.get(account_name='UACS Option Supplier')
		extras_opt = supplier_opt.raw_import.get('supplier_extras') or {}
		self.assertEqual(extras_opt.get('account_title'), 'Traveling Expenses - Local')
		self.assertEqual(extras_opt.get('uacs'), '5020603000')

		supplier_grp = Supplier.objects.get(account_name='UACS Group Supplier')
		extras_grp = supplier_grp.raw_import.get('supplier_extras') or {}
		self.assertEqual(extras_grp.get('account_title'), 'Training Expenses')
		self.assertEqual(extras_grp.get('uacs'), '5020201000')

	def test_supplier_import_auto_detects_custom_tax_rates(self):
		from cashier.models import FundCluster
		FundCluster.objects.get_or_create(code='164', defaults={'name': 'General Fund'})

		# Row with EWT calculated at 1.00% (e.g. goods purchase under VAT)
		rows = [
			{
				'PAYEE': 'Auto Rate Detection Supplier',
				'OR NUMBER': '1-90010',
				'AMOUNT': '3686.81',
				'TAX 5%': '173.91',
				'TAX 2%': '34.78',
				'GROSS AMOUNT': '3895.50',
				'FUND CLUSTER': '164',
			}
		]

		from admin_panel import views
		count, warnings = views._do_import('suppliers', rows, self.admin)
		self.assertEqual(count, 1)

		supplier = Supplier.objects.get(account_name='Auto Rate Detection Supplier')
		extras = supplier.raw_import.get('supplier_extras') or {}
		self.assertEqual(extras.get('tax_5_rate_vat'), '5.00')
		self.assertEqual(extras.get('tax_2_rate_vat'), '1.00')

		# Verify fallback calculation in _enrich_supplier_for_display resolves to the same values
		# when raw tax values are None (testing the mathematical resolution logic)
		extras['tax_5'] = None
		extras['tax_2'] = None
		supplier.raw_import['supplier_extras'] = extras
		supplier.save()

		views._enrich_supplier_for_display(supplier)
		self.assertEqual(supplier.tax_5, '173.91')
		self.assertEqual(supplier.tax_2, '34.78')
		self.assertEqual(supplier.gross_amount, '3895.50')

		# Verify compute_bucket_taxes uses default 2% rate for EWT -> 70.23 EWT, 175.56 VAT, 3932.60 Gross
		res = views.compute_bucket_taxes(Decimal('3686.81'))
		self.assertEqual(res['tax_5'], Decimal('175.56'))
		self.assertEqual(res['tax_2'], Decimal('70.23'))
		self.assertEqual(res['gross'], Decimal('3932.60'))


class ChequeDeletionTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_superuser(username='admin', email='admin@example.com', password='pass12345')
		self.client.force_login(self.admin)
		self.fund = FundCluster.objects.create(code='FC-1', name='Fund 1', balance=Decimal('5000.00'))
		self.cheque = Cheque.objects.create(
			cheque_number='111111',
			payee_name='Test Payee',
			amount=Decimal('1000.00'),
			date=date.today(),
			status='draft',
			created_by=self.admin,
			fund_cluster=self.fund
		)

	def test_delete_draft_cheque_succeeds(self):
		response = self.client.post(reverse('cheque_delete', args=[self.cheque.pk]))
		self.assertEqual(response.status_code, 302)
		self.assertFalse(Cheque.objects.filter(pk=self.cheque.pk).exists())

	def test_delete_released_cheque_fails(self):
		self.cheque.status = 'released'
		self.cheque.save()
		response = self.client.post(reverse('cheque_delete', args=[self.cheque.pk]))
		self.assertEqual(response.status_code, 302)
		self.assertIn('error=Released%20cheques%20cannot%20be%20deleted.', response.url)
		self.assertTrue(Cheque.objects.filter(pk=self.cheque.pk).exists())

	def test_delete_released_cheque_ajax_fails(self):
		self.cheque.status = 'released'
		self.cheque.save()
		response = self.client.post(
			reverse('cheque_delete', args=[self.cheque.pk]),
			HTTP_X_REQUESTED_WITH='XMLHttpRequest'
		)
		self.assertEqual(response.status_code, 400)
		data = response.json()
		self.assertFalse(data['ok'])
		self.assertEqual(data['error'], 'Released cheques cannot be deleted.')
		self.assertTrue(Cheque.objects.filter(pk=self.cheque.pk).exists())


class ChequeNumberFormatTests(TestCase):
	def test_normalize_cheque_number_freeform(self):
		# Alpha-numeric formats and spaces/hyphens are allowed
		self.assertEqual(Cheque.normalize_cheque_number('ABC-123'), 'ABC-123')
		self.assertEqual(Cheque.normalize_cheque_number('123 456 XY'), '123 456 XY')
		self.assertEqual(Cheque.normalize_cheque_number('MAY 26-001'), 'MAY 26-001')

	def test_normalize_cheque_number_padding(self):
		# Purely numeric short strings are still zero-padded to width (default 6)
		self.assertEqual(Cheque.normalize_cheque_number('123'), '000123')
		self.assertEqual(Cheque.normalize_cheque_number('5'), '000005')
		# If numeric and already at or above width, it is kept as-is
		self.assertEqual(Cheque.normalize_cheque_number('1234567'), '1234567')

	def test_record_generated_cheque_number_handles_freeform(self):
		# Non-numeric string shouldn't crash record_generated_cheque_number
		res = Cheque.record_generated_cheque_number('ABC-123')
		self.assertEqual(res, 'ABC-123')


class AboutSystemTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_superuser(username='admin', email='admin@example.com', password='pass12345')
		self.client.force_login(self.admin)

	def test_about_system_accessible(self):
		response = self.client.get(reverse('about_system'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Dee Jay B. Cristobal')
		self.assertContains(response, 'Registry Cheque Management System')
		self.assertContains(response, 'Cashiering Services')


class AboutCashierTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_superuser(username='admin', email='admin@example.com', password='pass12345')
		self.client.force_login(self.admin)

	def test_about_cashier_redirects_to_about(self):
		response = self.client.get(reverse('about_cashier'))
		self.assertEqual(response.status_code, 302)
		self.assertRedirects(response, reverse('about_system'))


class DisbursingOfficerTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_superuser(username='admin', email='admin@example.com', password='pass12345')
		self.client.force_login(self.admin)
		self.cashier1 = User.objects.create_user(username='cashier1', email='cashier1@example.com', password='pass12345', first_name='John', last_name='Doe')
		self.cashier2 = User.objects.create_user(username='cashier2', email='cashier2@example.com', password='pass12345', first_name='Jane', last_name='Smith')
		
		# create profiles
		from cashier.models import Profile
		self.profile1, _ = Profile.objects.get_or_create(user=self.cashier1, defaults={'role': 'cashier'})
		self.profile2, _ = Profile.objects.get_or_create(user=self.cashier2, defaults={'role': 'cashier'})

	def test_get_certification_name_defaults_to_passed_user(self):
		from admin_panel.views import _get_certification_name
		name = _get_certification_name(self.cashier1)
		self.assertIn('John', name)
		self.assertIn('Doe', name)

	def test_get_certification_name_retrieves_disbursing_officer(self):
		from admin_panel.views import _get_certification_name
		self.profile2.is_disbursing_officer = True
		self.profile2.save()
		
		name = _get_certification_name(self.cashier1)
		self.assertIn('Jane', name)
		self.assertIn('Smith', name)

	def test_create_user_with_disbursing_officer_fails_if_already_set(self):
		self.profile1.is_disbursing_officer = True
		self.profile1.save()

		post_data = {
			'action': 'create',
			'admin_password': 'pass12345',
			'first_name': 'Alice',
			'last_name': 'Brown',
			'middle_initial': 'B',
			'email': 'alice@example.com',
			'username': 'alice1',
			'password': 'Password123!',
			'role': 'cashier',
			'is_disbursing_officer': 'on',
		}
		response = self.client.post(reverse('user_management'), data=post_data)
		# Should return 200 with an error, not 302 redirect
		self.assertEqual(response.status_code, 200)
		self.assertIn("A Disbursing Officer is already set", response.content.decode())

		self.profile1.refresh_from_db()
		self.assertTrue(self.profile1.is_disbursing_officer)
		# User alice1 should not have been created
		self.assertFalse(User.objects.filter(username='alice1').exists())

	def test_create_user_with_disbursing_officer_succeeds_if_none_set(self):
		self.profile1.is_disbursing_officer = False
		self.profile1.save()

		post_data = {
			'action': 'create',
			'admin_password': 'pass12345',
			'first_name': 'Alice',
			'last_name': 'Brown',
			'middle_initial': 'B',
			'email': 'alice@example.com',
			'username': 'alice1',
			'password': 'Password123!',
			'role': 'cashier',
			'is_disbursing_officer': 'on',
		}
		response = self.client.post(reverse('user_management'), data=post_data)
		self.assertEqual(response.status_code, 302)

		from cashier.models import Profile
		alice_profile = Profile.objects.get(user__username='alice1')
		self.assertTrue(alice_profile.is_disbursing_officer)

	def test_edit_user_with_disbursing_officer_fails_if_already_set(self):
		self.profile1.is_disbursing_officer = True
		self.profile1.save()

		post_data = {
			'action': 'edit',
			'user_id': self.cashier2.id,
			'admin_password': 'pass12345',
			'first_name': 'Jane',
			'last_name': 'Smith',
			'middle_initial': 'S',
			'email': 'cashier2@example.com',
			'username': 'cashier2',
			'role': 'cashier',
			'is_disbursing_officer': 'on',
		}
		response = self.client.post(reverse('user_management'), data=post_data)
		self.assertEqual(response.status_code, 200)
		self.assertIn("A Disbursing Officer is already set", response.content.decode())

		self.profile1.refresh_from_db()
		self.profile2.refresh_from_db()
		self.assertTrue(self.profile1.is_disbursing_officer)
		self.assertFalse(self.profile2.is_disbursing_officer)

	def test_edit_user_with_disbursing_officer_succeeds_if_none_set(self):
		self.profile1.is_disbursing_officer = False
		self.profile1.save()

		post_data = {
			'action': 'edit',
			'user_id': self.cashier2.id,
			'admin_password': 'pass12345',
			'first_name': 'Jane',
			'last_name': 'Smith',
			'middle_initial': 'S',
			'email': 'cashier2@example.com',
			'username': 'cashier2',
			'role': 'cashier',
			'is_disbursing_officer': 'on',
		}
		response = self.client.post(reverse('user_management'), data=post_data)
		self.assertEqual(response.status_code, 302)

		self.profile2.refresh_from_db()
		self.assertTrue(self.profile2.is_disbursing_officer)

	def test_employee_id_auto_generation(self):
		# Clear existing employee IDs
		from cashier.models import Profile
		Profile.objects.all().update(employee_id=None)

		# Add some manual/custom IDs for all profiles so none are empty
		admin_profile = Profile.objects.get(user=self.admin)
		admin_profile.employee_id = "EMP-2024-00001"
		admin_profile.save()

		self.profile1.employee_id = "EMP-2024-00158"
		self.profile1.save()
		self.profile2.employee_id = "159"
		self.profile2.save()

		response = self.client.get(reverse('user_management'))
		self.assertEqual(response.status_code, 200)
		next_employee_id = response.context['next_employee_id']
		
		# The max ID number was 159 (from "159"). The next should be 160.
		from datetime import datetime
		expected_year = datetime.now().year
		self.assertEqual(next_employee_id, f"EMP-{expected_year}-00160")

	def test_create_user_with_admin_role_fails_if_already_set(self):
		# Set profile1 as admin
		self.profile1.role = 'admin'
		self.profile1.save()

		post_data = {
			'action': 'create',
			'admin_password': 'pass12345',
			'first_name': 'Alice',
			'last_name': 'Brown',
			'middle_initial': 'B',
			'email': 'alice@example.com',
			'username': 'alice1',
			'password': 'Password123!',
			'role': 'admin',
		}
		response = self.client.post(reverse('user_management'), data=post_data)
		self.assertEqual(response.status_code, 200)
		self.assertIn("An Admin account is already set", response.content.decode())
		
		# User alice1 should not have been created
		self.assertFalse(User.objects.filter(username='alice1').exists())

	def test_edit_user_to_admin_role_fails_if_already_set(self):
		# Set profile1 as admin
		self.profile1.role = 'admin'
		self.profile1.save()

		post_data = {
			'action': 'edit',
			'user_id': self.cashier2.id,
			'admin_password': 'pass12345',
			'first_name': 'Jane',
			'last_name': 'Smith',
			'middle_initial': 'S',
			'email': 'cashier2@example.com',
			'username': 'cashier2',
			'role': 'admin',
		}
		response = self.client.post(reverse('user_management'), data=post_data)
		self.assertEqual(response.status_code, 200)
		self.assertIn("An Admin account is already set", response.content.decode())

		self.profile2.refresh_from_db()
		self.assertEqual(self.profile2.role, 'cashier')

	def test_missing_employee_id_auto_regeneration_on_get(self):
		from cashier.models import Profile
		# Set profile1's employee_id to None
		self.profile1.employee_id = None
		self.profile1.save()

		# Ensure profile2 and admin have starting IDs so we control max suffix
		self.profile2.employee_id = "EMP-2024-00100"
		self.profile2.save()
		admin_profile = Profile.objects.get(user=self.admin)
		admin_profile.employee_id = "EMP-2024-00001"
		admin_profile.save()

		# Load the user management page
		response = self.client.get(reverse('user_management'))
		self.assertEqual(response.status_code, 200)

		# profile1 should have been automatically assigned the next ID: EMP-YYYY-00101
		self.profile1.refresh_from_db()
		self.assertIsNotNone(self.profile1.employee_id)
		self.assertTrue(self.profile1.employee_id.startswith("EMP-"))
		self.assertIn("00101", self.profile1.employee_id)




class SystemResetTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_superuser(username='admin', email='admin@example.com', password='pass12345')
		self.client.force_login(self.admin)
		
		# Create extra non-admin user
		self.other_user = User.objects.create_user(username='cashier1', email='cashier1@example.com', password='pass12345')
		from cashier.models import Profile, Transaction, Cheque, Supplier, Report, FundCluster, RoleConfig
		from admin_panel.models import AuditLog, SystemSetting, AccountTitleGroup, ManagementOption
		Profile.objects.get_or_create(user=self.other_user, defaults={'role': 'cashier'})
		Profile.objects.get_or_create(user=self.admin, defaults={'role': 'admin'})
		
		# Create transactional data
		self.supplier = Supplier.objects.create(account_name="Supplier Alpha")
		self.cheque = Cheque.objects.create(cheque_number="123456", payee=self.supplier, amount=Decimal("500.00"))
		self.transaction = Transaction.objects.create(student_name="Alice", amount=Decimal("100.00"))
		self.report = Report.objects.create(title="Monthly Report", report_type="cheque_summary", generated_by=self.admin)
		
		# Create operational/config data
		self.fund_cluster = FundCluster.objects.create(code="101", name="General Fund", balance=Decimal("1000.00"))
		self.account_group = AccountTitleGroup.objects.create(name="Cash in Bank")
		self.mgmt_option = ManagementOption.objects.create(category=ManagementOption.CATEGORY_ACCOUNT_TITLE, value="Cash")
		self.role_config = RoleConfig.objects.create(role="cashier", config={})
		
		# Clear existing SystemSetting and create custom
		SystemSetting.objects.all().delete()
		self.system_setting = SystemSetting.objects.create(pk=1, system_name="registry_custom")
		
		AuditLog.objects.create(admin=self.admin, action="Initial Setup")

	def test_system_reset_success_with_correct_password(self):
		from cashier.models import Profile, Transaction, Cheque, Supplier, Report, FundCluster, RoleConfig
		from admin_panel.models import AuditLog, SystemSetting, AccountTitleGroup, ManagementOption

		# Verify initial counts
		self.assertEqual(User.objects.count(), 2)
		self.assertEqual(Profile.objects.count(), 2)
		self.assertEqual(Supplier.objects.count(), 1)
		self.assertEqual(Cheque.objects.count(), 1)
		self.assertEqual(Transaction.objects.count(), 1)
		self.assertEqual(Report.objects.count(), 1)
		self.assertEqual(FundCluster.objects.count(), 1)
		self.assertEqual(AccountTitleGroup.objects.count(), 1)
		self.assertEqual(ManagementOption.objects.count(), 1)
		self.assertEqual(RoleConfig.objects.count(), 1)
		self.assertEqual(SystemSetting.objects.count(), 1)
		self.assertEqual(AuditLog.objects.count(), 1)

		post_data = {
			'action': 'reset',
			'admin_password': 'pass12345',
		}
		response = self.client.post(reverse('backup_restore'), data=post_data)
		self.assertEqual(response.status_code, 200)
		self.assertIn("System has been successfully reset", response.context['success'])

		# Verify all database data is deleted
		self.assertEqual(Supplier.objects.count(), 0)
		self.assertEqual(Cheque.objects.count(), 0)
		self.assertEqual(Transaction.objects.count(), 0)
		self.assertEqual(Report.objects.count(), 0)
		self.assertEqual(FundCluster.objects.count(), 0)
		self.assertEqual(AccountTitleGroup.objects.count(), 0)
		self.assertEqual(ManagementOption.objects.count(), 0)
		self.assertEqual(RoleConfig.objects.count(), 0)

		# SystemSetting should have exactly 1 re-initialized record (the default system setting)
		self.assertEqual(SystemSetting.objects.count(), 1)
		self.assertEqual(SystemSetting.objects.first().system_name, "registry")

		# Verify non-admin users/profiles are deleted but admin user is preserved
		self.assertEqual(User.objects.count(), 1)
		self.assertEqual(User.objects.first(), self.admin)
		self.assertEqual(Profile.objects.count(), 1)
		self.assertEqual(Profile.objects.first().user, self.admin)

		# AuditLog should contain exactly the "System reset executed" action
		self.assertEqual(AuditLog.objects.count(), 1)
		self.assertEqual(AuditLog.objects.first().action, "System reset executed")

	def test_system_reset_failure_with_wrong_password(self):
		from cashier.models import Profile, Transaction, Cheque, Supplier, Report, FundCluster, RoleConfig
		from admin_panel.models import AuditLog, SystemSetting, AccountTitleGroup, ManagementOption

		# Verify initial counts
		self.assertEqual(User.objects.count(), 2)
		self.assertEqual(Profile.objects.count(), 2)
		self.assertEqual(Supplier.objects.count(), 1)
		self.assertEqual(Cheque.objects.count(), 1)
		self.assertEqual(Transaction.objects.count(), 1)
		self.assertEqual(Report.objects.count(), 1)
		self.assertEqual(FundCluster.objects.count(), 1)
		self.assertEqual(AccountTitleGroup.objects.count(), 1)
		self.assertEqual(ManagementOption.objects.count(), 1)
		self.assertEqual(RoleConfig.objects.count(), 1)
		self.assertEqual(SystemSetting.objects.count(), 1)
		self.assertEqual(AuditLog.objects.count(), 1)

		post_data = {
			'action': 'reset',
			'admin_password': 'wrongpassword',
		}
		response = self.client.post(reverse('backup_restore'), data=post_data)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['error'], "Admin password is incorrect")

		# Verify data is untouched
		self.assertEqual(User.objects.count(), 2)
		self.assertEqual(Profile.objects.count(), 2)
		self.assertEqual(Supplier.objects.count(), 1)
		self.assertEqual(Cheque.objects.count(), 1)
		self.assertEqual(Transaction.objects.count(), 1)
		self.assertEqual(Report.objects.count(), 1)
		self.assertEqual(FundCluster.objects.count(), 1)
		self.assertEqual(AccountTitleGroup.objects.count(), 1)
		self.assertEqual(ManagementOption.objects.count(), 1)
		self.assertEqual(RoleConfig.objects.count(), 1)
		self.assertEqual(SystemSetting.objects.count(), 1)
		self.assertEqual(AuditLog.objects.count(), 1)


class CashierPermissionsTests(TestCase):
	def setUp(self):
		self.cashier_user = User.objects.create_user(username='cashier_test', email='cashier_test@example.com', password='pass12345')
		from cashier.models import Profile
		Profile.objects.get_or_create(user=self.cashier_user, defaults={'role': 'cashier'})
		self.client.force_login(self.cashier_user)

	def test_cashier_can_add_supplier(self):
		response = self.client.post(reverse('suppliers'), {
			'action': 'create',
			'account_name': 'New Supplier Ltd',
			'amount': '150.00',
			'status': 'active'
		})
		self.assertEqual(response.status_code, 200) # view renders 200 with success
		from cashier.models import Supplier
		self.assertTrue(Supplier.objects.filter(account_name='New Supplier Ltd').exists())

	def test_cashier_can_edit_own_supplier(self):
		from cashier.models import Supplier
		sup = Supplier.objects.create(account_name='Old Name', created_by=self.cashier_user)
		response = self.client.post(reverse('suppliers'), {
			'action': 'edit',
			'sup_id': sup.pk,
			'account_name': 'Updated Name',
			'amount': '250.00',
			'status': 'active'
		})
		self.assertEqual(response.status_code, 200)
		sup.refresh_from_db()
		self.assertEqual(sup.account_name, 'Updated Name')

	def test_cashier_can_edit_own_cheque(self):
		from cashier.models import Cheque, Supplier
		sup = Supplier.objects.create(account_name='Payee', created_by=self.cashier_user)
		cheque = Cheque.objects.create(cheque_number='999999', payee=sup, amount=Decimal('100.00'), created_by=self.cashier_user, status='draft')
		response = self.client.post(reverse('cheque_edit', args=[cheque.pk]), {
			'cheque_number': '111111',
			'amount': '200.00',
			'payee_id': sup.pk,
			'date': '2026-08-04'
		})
		self.assertEqual(response.status_code, 302) # Redirects on success
		cheque.refresh_from_db()
		self.assertEqual(cheque.cheque_number, '111111')

	def test_cashier_can_delete_own_cheque(self):
		from cashier.models import Cheque, Supplier
		sup = Supplier.objects.create(account_name='Payee', created_by=self.cashier_user)
		cheque = Cheque.objects.create(cheque_number='999999', payee=sup, amount=Decimal('100.00'), created_by=self.cashier_user, status='draft')
		response = self.client.post(reverse('cheque_delete', args=[cheque.pk]))
		self.assertEqual(response.status_code, 302)
		from cashier.models import Cheque
		self.assertFalse(Cheque.objects.filter(pk=cheque.pk).exists())
