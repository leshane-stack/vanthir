"""
Backfill prior-year assessed/market/taxable values from the Miami-Dade PA public
proxy (source = miami_dade_tax_roll). SEPARATE from ingest_appraiser.

Joins by folio off the Parcel spine: it iterates parcels already ingested by the
appraiser adapter, so run ingest_appraiser first. Stay scoped to a ZIP/slice —
do not run county-wide.

Examples:
  python manage.py ingest_historical_assessments --folio 0102100301130
  python manage.py ingest_historical_assessments --zip 33131 --limit 200
"""

from django.core.management.base import BaseCommand, CommandError

from properties.ingest_historical_assessments import run_historical_ingest
from properties.models import Parcel


class Command(BaseCommand):
    help = "Backfill prior-year AssessmentSnapshots from the Miami-Dade PA proxy."

    def add_arguments(self, parser):
        parser.add_argument("--folio", help="Backfill a single folio.")
        parser.add_argument("--zip", help="Backfill all ingested parcels in this site ZIP.")
        parser.add_argument("--limit", type=int, help="Max folios to process.")

    def handle(self, *args, **opts):
        if opts.get("folio"):
            folios = [opts["folio"]]
        elif opts.get("zip"):
            qs = (
                Parcel.objects.filter(zip_code__startswith=opts["zip"])
                .order_by("folio")
                .values_list("folio", flat=True)
            )
            if opts.get("limit"):
                qs = qs[: opts["limit"]]
            folios = list(qs)
            if not folios:
                raise CommandError(
                    f"No ingested parcels in ZIP {opts['zip']}. Run ingest_appraiser first."
                )
        else:
            raise CommandError("Provide --folio or --zip (county-wide runs are not allowed).")

        self.stdout.write(self.style.NOTICE(f"Backfilling history for {len(folios)} folio(s)..."))
        count = run_historical_ingest(folios, stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(f"Ingested history for {count} folio(s)."))
