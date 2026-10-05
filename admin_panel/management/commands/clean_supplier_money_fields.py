from decimal import Decimal, InvalidOperation
from django.core.management.base import BaseCommand


def clean_money(val):
    """Strip peso signs, commas, whitespace; return clean numeric string or ''."""
    if val is None:
        return ''
    raw = str(val).strip().replace(',', '').replace('₱', '').replace('\u20B1', '')
    if not raw:
        return ''
    try:
        return str(Decimal(raw).quantize(Decimal('0.01')))
    except (InvalidOperation, ValueError):
        return ''


class Command(BaseCommand):
    help = 'Clean peso signs, commas, and NaN strings from supplier money fields in raw_import'

    def handle(self, *args, **options):
        from cashier.models import Supplier

        money_keys = ('other_deductions', 'professional_tax', 'tax_5', 'tax_2', 'gross_amount')
        fixed = 0
        skipped = 0

        for sup in Supplier.objects.all():
            if not sup.raw_import:
                continue
            extras = (sup.raw_import or {}).get('supplier_extras') or {}
            changed = False
            for key in money_keys:
                old_val = extras.get(key)
                if old_val is None:
                    continue
                new_val = clean_money(old_val)
                if new_val != str(old_val):
                    extras[key] = new_val
                    changed = True
            if changed:
                sup.raw_import = {**sup.raw_import, 'supplier_extras': extras}
                sup.save(update_fields=['raw_import'])
                fixed += 1
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(f'Done. Fixed: {fixed}, Skipped: {skipped}'))
