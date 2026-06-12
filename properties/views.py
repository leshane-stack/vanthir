from django.shortcuts import get_object_or_404, render

from properties.models import Parcel
from properties.parcel_page import build_parcel_page


def parcel_detail(request, folio):
    """Render one parcel's public page. Thin/junk parcels (see
    Parcel.is_indexable) still render for a human but are marked noindex by the
    template, so they never enter the search index as thin pages."""
    parcel = get_object_or_404(Parcel, folio=folio)
    context = build_parcel_page(parcel)
    return render(request, "properties/parcel_detail.html", context)
