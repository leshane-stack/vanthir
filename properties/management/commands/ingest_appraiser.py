"""
Ingest Miami-Dade Property Appraiser data.

Examples:
  python manage.py ingest_appraiser --folio 0101000000020   # one parcel
  python manage.py ingest_appraiser --limit 50              # first 50
  python manage.py ingest_appraiser --zip 33131 --limit 500 # a ZIP, capped
  python manage.py ingest_appraiser                         # full county (large)
"""

from django.core.management.base import BaseCommand

from properties.ingest import run_ingest


class Command(BaseCommand):
    help = "Ingest Miami-Dade Property Appraiser parcels into the data model."

    def add_arguments(self, parser):
        parser.add_argument("--folio", help="Ingest a single folio.")
        parser.add_argument("--zip", help="Ingest a single site ZIP code.")
        parser.add_argument("--where", help="Raw ArcGIS WHERE clause (advanced).")
        parser.add_argument("--limit", type=int, help="Max records to ingest.")
        parser.add_argument("--page-size", type=int, default=1000, help="Records per request.")

    def handle(self, *args, **opts):
        if opts.get("where"):
            where = opts["where"]
        elif opts.get("folio"):
            where = f"FOLIO='{opts['folio']}'"
        elif opts.get("zip"):
            where = f"TRUE_SITE_ZIP_CODE LIKE '{opts['zip']}%'"
        else:
            where = "1=1"

        self.stdout.write(self.style.NOTICE(f"WHERE {where}"))
        count = run_ingest(
            where=where,
            limit=opts.get("limit"),
            page_size=opts["page_size"],
            stdout=self.stdout,
        )
        self.stdout.write(self.style.SUCCESS(f"Ingested {count} parcel(s)."))
