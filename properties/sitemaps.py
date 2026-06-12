"""
Sitemap for public parcel pages.

Lists ONLY indexable parcels (Parcel.objects.indexable() — the set-level twin of
the thin-page guard), so reference folios, common-area parcels, and address-less
/ $0 parcels never appear. The query is not a hardcoded list: it reflects whatever
is in the DB, so the sitemap auto-grows as more ZIPs are ingested.

Domain/scheme are not hardcoded — the sitemap view derives the host from the
request (RequestSite, since django.contrib.sites is not installed), so the same
code serves correct URLs locally and in production. protocol is pinned to https
for the canonical production URLs.
"""

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from properties.models import Parcel


class ParcelSitemap(Sitemap):
    protocol = "https"
    changefreq = "monthly"
    priority = 0.6

    def items(self):
        return Parcel.objects.indexable().order_by("folio")

    def location(self, parcel):
        return reverse("parcel_detail", args=[parcel.folio])

    def lastmod(self, parcel):
        return parcel.updated_at
