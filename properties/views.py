from django.shortcuts import get_object_or_404, redirect, render

from properties.models import Parcel
from properties.parcel_page import build_parcel_page, pretty_address

CANONICAL_URL = "https://vanthir.com/"


def _example_parcels(limit=8):
    """A handful of real, data-rich indexable parcels to seed a clickable path
    into the parcel pages. Selected dynamically (never hardcoded) so it stays
    valid as more data is ingested: indexable, has a building + a recorded sale,
    biggest first."""
    qs = (
        Parcel.objects.indexable()
        .filter(living_sqft__isnull=False, deeds__isnull=False)
        .distinct()
        .order_by("-living_sqft")[:limit]
    )
    return [
        {
            "folio": p.folio,
            "address": pretty_address(p.address_line) or p.folio,
            "city": p.city,
            "land_use": p.land_use,
            "year_built": p.year_built,
        }
        for p in qs
    ]


def home(request):
    """Canonical front door at /. Indexable; introduces Vanthir and links into
    real parcel pages."""
    return render(request, "properties/home.html", {
        "examples": _example_parcels(),
        "canonical_url": CANONICAL_URL,
        "is_search": False,
    })


def search(request):
    """Lightweight lookup: a folio routes straight to its page; otherwise an
    address substring search over indexable parcels. Results pages are noindex."""
    q = (request.GET.get("q") or "").strip()
    if not q:
        return redirect("home")

    # Direct folio lookup (with or without dashes) -> straight to the parcel.
    folio_guess = q.replace("-", "").replace(" ", "")
    if folio_guess.isdigit() and Parcel.objects.filter(folio=folio_guess).exists():
        return redirect("parcel_detail", folio=folio_guess)

    matches = (
        Parcel.objects.indexable()
        .filter(address_line__icontains=q)
        .order_by("address_line")[:25]
    )
    results = [
        {
            "folio": p.folio,
            "address": pretty_address(p.address_line) or p.folio,
            "city": p.city,
            "land_use": p.land_use,
        }
        for p in matches
    ]
    if len(results) == 1:
        return redirect("parcel_detail", folio=results[0]["folio"])

    return render(request, "properties/home.html", {
        "examples": _example_parcels(),
        "query": q,
        "results": results,
        "is_search": True,
        "canonical_url": CANONICAL_URL,
    })


def parcel_detail(request, folio):
    """Render one parcel's public page. Thin/junk parcels (see
    Parcel.is_indexable) still render for a human but are marked noindex by the
    template, so they never enter the search index as thin pages."""
    parcel = get_object_or_404(Parcel, folio=folio)
    context = build_parcel_page(parcel)
    return render(request, "properties/parcel_detail.html", context)
