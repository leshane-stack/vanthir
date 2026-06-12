"""
Build the render context for a single parcel page.

Everything here is computed from ONE parcel's own ingested rows — no cross-parcel
data, no entity resolution (owner stays the verbatim raw string), no new sources.
The FAQ answer strings are built once and reused for both the visible FAQ block
and the FAQPage JSON-LD, so the structured data always matches the page text.
"""

import json
from datetime import date

TAX_ROLL_SOURCE = "miami_dade_tax_roll"
APPRAISER_SOURCE = "miami_dade_pa"
APPRAISER_LABEL = "Miami-Dade Property Appraiser"
TAX_ROLL_LABEL = "Miami-Dade Property Appraiser tax roll"

# Directionals to keep uppercase when prettifying ALL-CAPS county addresses.
_DIRECTIONALS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


def pretty_address(raw):
    """ '41 E FLAGLER ST' -> '41 E Flagler St' (keeps SE/NW/etc. uppercase)."""
    if not raw:
        return ""
    out = []
    for word in raw.split():
        out.append(word if word.upper() in _DIRECTIONALS else word.capitalize())
    return " ".join(out)


def dollars(value):
    if value is None:
        return "—"
    return "${:,.0f}".format(value)


def _pct_change(first, last):
    if not first or last is None:
        return None
    return (float(last) - float(first)) / float(first) * 100.0


def build_parcel_page(parcel):
    """Return the template context for `parcel`."""
    addr = pretty_address(parcel.address_line) or parcel.folio
    city_state_zip = " ".join(
        p for p in [parcel.city, parcel.state, parcel.zip_code] if p
    )

    # --- Owner (raw string only; never resolved) ---------------------------
    own = parcel.ownership_snapshots.order_by("-observed_at").first()
    owner_raw = own.owner_name_raw if own else None
    owner_observed = own.observed_at.date() if own else None

    # --- Sale history (from Deeds) -----------------------------------------
    deeds = list(parcel.deeds.order_by("-recorded_date", "-effective_date"))
    last_sale = deeds[0] if deeds else None
    sale_rows = [
        {
            "recorded_date": d.recorded_date,
            "price_display": dollars(d.sale_price),
            "grantee_raw": d.grantee_raw,
        }
        for d in deeds
    ]

    # --- 3-year assessed-value trajectory (the differentiated content) -----
    snaps = list(
        parcel.assessments.filter(source=TAX_ROLL_SOURCE)
        .exclude(assessed_value__isnull=True)
        .order_by("tax_year")
    )
    max_assessed = max((s.assessed_value for s in snaps), default=None)
    trajectory = []
    for s in snaps:
        pct_of_max = (
            float(s.assessed_value) / float(max_assessed) * 100.0
            if max_assessed else 0.0
        )
        trajectory.append({
            "year": s.tax_year,
            "assessed": s.assessed_value,
            "assessed_display": dollars(s.assessed_value),
            "market": s.market_value,
            "market_display": dollars(s.market_value),
            "taxable": s.taxable_value,
            "taxable_display": dollars(s.taxable_value),
            "bar_pct": round(pct_of_max, 1),
        })
    net_pct = (
        _pct_change(trajectory[0]["assessed"], trajectory[-1]["assessed"])
        if len(trajectory) >= 2 else None
    )
    traj_years = (
        f"{trajectory[0]['year']}–{trajectory[-1]['year']}" if trajectory else None
    )

    # --- Flood zone (only if ingested) -------------------------------------
    flood = parcel.flood_zones.order_by("-observed_at").first()

    # --- Characteristics (show residential beds/baths only if meaningful) --
    chars = []
    if parcel.year_built:
        chars.append(("Year built", str(parcel.year_built)))
    if parcel.living_sqft:
        chars.append(("Living area", f"{parcel.living_sqft:,} sqft"))
    if parcel.beds:
        chars.append(("Beds", str(parcel.beds)))
    if parcel.baths:
        chars.append(("Baths", str(parcel.baths)))
    if parcel.land_use:
        chars.append(("Land use", parcel.land_use))

    # --- Computed FAQ (answers reused verbatim in JSON-LD) ------------------
    faqs = []

    # Q1 — owner
    if owner_raw:
        a = (
            f"The owner of record is {owner_raw}. This is the verbatim owner "
            f"string from the {APPRAISER_LABEL}"
        )
        a += f" (observed {owner_observed:%B %-d, %Y})." if owner_observed else "."
    else:
        a = f"No owner of record has been recorded for this parcel in the {APPRAISER_LABEL} data."
    faqs.append((f"Who owns {addr}?", a))

    # Q2 — value trajectory
    if len(trajectory) >= 2:
        steps = ", ".join(
            f"{dollars(t['assessed'])} ({t['year']})" for t in trajectory
        )
        direction = (
            "risen" if net_pct and net_pct > 0.5
            else "fallen" if net_pct and net_pct < -0.5
            else "stayed roughly flat"
        )
        a = (
            f"Its assessed value went {steps}. Over {traj_years} the assessed "
            f"value has {direction}"
        )
        a += f" ({net_pct:+.1f}%)." if net_pct is not None else "."
        a += f" Source: {TAX_ROLL_LABEL}, {traj_years}."
    elif trajectory:
        t = trajectory[0]
        a = (
            f"The only assessed value on record is {dollars(t['assessed'])} for "
            f"{t['year']} ({TAX_ROLL_LABEL}); not enough years to show a trend yet."
        )
    else:
        a = "No multi-year assessed-value history has been recorded for this parcel yet."
    faqs.append((f"How has {addr}'s assessed value changed?", a))

    # Q3 — last sale
    if last_sale and last_sale.recorded_date:
        price = (
            f" for {dollars(last_sale.sale_price)}" if last_sale.sale_price else ""
        )
        a = (
            f"It last sold on {last_sale.recorded_date:%B %-d, %Y}{price}, per the "
            f"recorded deed ({APPRAISER_LABEL})."
        )
    else:
        a = f"No recorded sale is on file for this parcel in the {APPRAISER_LABEL} deed records."
    faqs.append((f"When was {addr} last sold?", a))

    # Q4 — flood risk
    if flood and (flood.zone or flood.in_sfha is not None):
        sfha = (
            "in a FEMA Special Flood Hazard Area" if flood.in_sfha
            else "not in a FEMA Special Flood Hazard Area" if flood.in_sfha is not None
            else "of unspecified Special Flood Hazard Area status"
        )
        zone = f"FEMA flood zone {flood.zone}" if flood.zone else "an unspecified FEMA flood zone"
        a = f"This parcel is in {zone} and is {sfha}."
    else:
        a = (
            "No FEMA flood-zone designation has been ingested for this parcel in "
            "Vanthir yet, so flood risk is not yet available from this record."
        )
    faqs.append((f"What is {addr}'s flood risk?", a))

    # FAQPage JSON-LD built from the SAME answer strings shown on the page.
    faq_schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": a},
            }
            for q, a in faqs
        ],
    }
    # Escape angle brackets so the blob can't break out of the <script> tag.
    faq_jsonld = (
        json.dumps(faq_schema, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )

    return {
        "parcel": parcel,
        "indexable": parcel.is_indexable,
        "faq_jsonld": faq_jsonld,
        "display_address": addr,
        "city_state_zip": city_state_zip,
        "owner_raw": owner_raw,
        "owner_observed": owner_observed,
        "characteristics": chars,
        "deeds": deeds,
        "sale_rows": sale_rows,
        "last_sale": last_sale,
        "trajectory": trajectory,
        "net_pct": net_pct,
        "traj_years": traj_years,
        "flood": flood,
        "faqs": faqs,
        "appraiser_label": APPRAISER_LABEL,
        "tax_roll_label": TAX_ROLL_LABEL,
    }
