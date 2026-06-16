"""Tests for the homepage, the folio/address search, and the canonical
www -> apex redirect. Scoped to the new front-door behavior."""

from datetime import date, datetime, timezone as dt_tz

from django.test import TestCase

from properties.models import Parcel, Deed, AssessmentSnapshot

OBS = datetime(2026, 6, 4, tzinfo=dt_tz.utc)


def _indexable(folio="0101110601160", addr="41 E FLAGLER ST", sqft=60426):
    p = Parcel.objects.create(
        folio=folio, county="Miami-Dade", state="FL",
        address_line=addr, city="Miami", zip_code="33131-0000",
        living_sqft=sqft, year_built=1928, land_use="STORE : RETAIL OUTLET",
    )
    Deed.objects.create(
        parcel=p, source="miami_dade_pa", source_record_id=f"{folio}:1",
        sale_price=27200000, recorded_date=date(2021, 5, 26), observed_at=OBS,
    )
    AssessmentSnapshot.objects.create(
        parcel=p, source="miami_dade_tax_roll", tax_year=2025,
        assessed_value=11211250, observed_at=OBS,
    )
    return p


class HomePageTests(TestCase):
    def setUp(self):
        self.p = _indexable()

    def test_root_returns_200_indexable_with_links(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)   # not 404 anymore
        html = r.content.decode()
        self.assertNotIn("noindex", html)       # canonical entry page
        self.assertIn("Vanthir", html)
        self.assertIn("Property intelligence for Miami-Dade", html)
        self.assertIn(f"/property/{self.p.folio}/", html)  # clickable path in

    def test_example_link_resolves(self):
        html = self.client.get("/").content.decode()
        self.assertIn("41 E Flagler St", html)
        # the linked parcel page itself renders
        self.assertEqual(self.client.get(f"/property/{self.p.folio}/").status_code, 200)

    def test_search_by_folio_redirects_to_parcel(self):
        r = self.client.get("/search/", {"q": self.p.folio})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], f"/property/{self.p.folio}/")

    def test_search_single_address_match_redirects(self):
        r = self.client.get("/search/", {"q": "41 E FLAGLER"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], f"/property/{self.p.folio}/")

    def test_search_results_list_is_noindex(self):
        _indexable(folio="0101110601140", addr="5 E FLAGLER ST", sqft=16364)
        r = self.client.get("/search/", {"q": "FLAGLER"})  # 2 matches -> list
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("noindex", html)
        self.assertIn("/property/0101110601160/", html)
        self.assertIn("/property/0101110601140/", html)

    def test_search_no_match(self):
        r = self.client.get("/search/", {"q": "nowhere-xyz"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("No matching", r.content.decode())

    def test_empty_search_redirects_home(self):
        r = self.client.get("/search/", {"q": "  "})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/")


class CanonicalRedirectTests(TestCase):
    def test_www_redirects_301_to_apex(self):
        r = self.client.get("/property/0101110601160/", HTTP_HOST="www.vanthir.com")
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r["Location"], "https://vanthir.com/property/0101110601160/")

    def test_www_preserves_query_string(self):
        r = self.client.get("/search/", {"q": "flagler"}, HTTP_HOST="www.vanthir.com")
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r["Location"], "https://vanthir.com/search/?q=flagler")

    def test_apex_not_redirected(self):
        _indexable()
        r = self.client.get("/", HTTP_HOST="vanthir.com")
        self.assertEqual(r.status_code, 200)
