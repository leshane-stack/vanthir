# Vanthir — Progress / Handoff

_Last updated: 2026-06-04_

## What's built and working
- **Data model** (`properties/models.py`): full schema honoring the five invariants —
  Parcel as spine, resolve-owners-later (`OwnershipSnapshot.owner_name_raw` verbatim,
  `owner_entity` null), source-on-everything (`SourcedRecord` + `RawSnapshot`), the FCRA
  wall (`CourtFiling.caption_raw` is opaque provenance, not a screening field), and
  scores-decompose-into-factors (`HealthScore.is_defensible`).
- **Appraiser ingest adapter** (`properties/ingest.py`): Miami-Dade Property Appraiser
  GIS FeatureServer. `fetch_page()` (network) is separated from `ingest_feature()` (pure
  mapping). Writes RawSnapshot first; upserts Parcel by folio; ownership append-only on
  change; assessment idempotent per (parcel, source, tax_year); deed deduped on
  (source, source_record_id).
- **Management command** `python manage.py ingest_appraiser` (`--folio` / `--zip` /
  `--where` / `--limit`).
- **Tests** (`properties/tests_ingest.py`): 5 passing (3 pre-existing + 2 added). Full
  suite: 9 passing.

## What this session added
1. **Initialized git** (repo had no `.git`) and made a base commit; project is now under
   version control so changes are reversible.
2. **Fixed a real slice-scale crash.** Running `ingest_appraiser --zip 33131 --limit 200`
   died with `decimal.InvalidOperation` on folio `0102100301130`, which carries an absurd
   county `BATTHROOM_COUNT = 1220`. `baths` is `DecimalField(4,1)` (max 999.9), so the
   value overflowed on save and aborted the whole batch. Added `_dec_fit(v, max_digits,
   decimal_places)` — returns the Decimal only if it fits the field, else `None` (treated
   as unknown, **not** clamped to a fabricated number). The verbatim value is never lost;
   it stays in the RawSnapshot payload. Applied to `baths`, `latitude`, `longitude` (the
   narrow-precision fields where county garbage realistically overflows).
3. **TDD for idempotency at slice scale** (`SliceScaleIdempotencyTests`): wrote the
   failing test first (it reproduced the 1220-baths crash), then made it pass. Covers
   (a) the absurd-baths record ingests as `baths=None` with raw 1220 preserved, and
   (b) re-running an identical multi-record slice does not duplicate deeds, same-year
   assessments, or unchanged-ownership rows.
4. Verified live idempotency end-to-end: ran the command **twice** against a clean DB.

## Data-quality findings — ZIP 33131, 200 parcels (clean single run)
| Field | Count |
|---|---|
| Parcels ingested | 200 |
| With owner string (OwnershipSnapshot) | 198 |
| With assessment (AssessmentSnapshot) | 200 |
| With sale/deed | 134 |
| With lat **and** lng | 200 |

**Live re-run (idempotency proof):** parcels 200→200, ownership 198→198, assessments
200→200, deeds 134→134. Only RawSnapshot grows (200→400) — intentional, provenance is
logged every scrape.

### Flagged / suspect — all explained, none are parser bugs
- **All 200 `assessed_value` are NULL.** The county GIS view's current cycle is tax year
  **2026**, and `ASSESSED_VAL_CUR` is genuinely empty in the source (assessment roll not
  certified yet as of 2026-06-04). `tax_year` is captured correctly. **Implication:** this
  single source yields no value *trajectory* yet — only the current (empty) year. The
  value-history moat needs prior-year roll data (a separate source/endpoint), out of scope
  this session.
- **2 parcels with no owner** (`0141019030001`, `0141019040001`): owner fields fully null
  in source (government / reference folios). Adapter correctly writes **no** OwnershipSnapshot
  rather than a blank `owner_name_raw` — invariant respected.
- **25 parcels with blank address**: all vacant land / parking lots / government / "REFERENCE
  FOLIO" — `TRUE_SITE_ADDR` genuinely null in source (unimproved parcels have no street address).
- **1 parcel with null baths** (`0102100301130`): the absurd 1220 value, now stored as
  unknown (raw preserved). No other absurd numerics.
- **year_built**: 76 null (vacant/unimproved); populated range **1920–2024** — all sane.
- **deeds**: `sale_price` null on 0 of 134 — adapter only writes a deed when both sale date
  and price are present.

## Session 2 (2026-06-04) — Historical assessments (prior-year value trajectory)

On branch `historical-assessments` (not merged). Two-phase: research, then build.

### Source found (research phase)
- **The appraiser GIS layer has no history.** Full audit of the Miami-Dade ArcGIS org
  (`8Pc9XBTAsYuxx9Ny`, 909 services): `PaGISView`/`PaParcelView` carry only
  `ASSESSED_VAL_CUR` (current cycle); the dated `Property2021Dec`/`Property2023Dec` layers
  are parcel-geometry only (no values); no sibling layer or related table has prior years.
- **Source used: Miami-Dade PA public JSON proxy** — `GetPropertySearchByFolio` at
  `https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx`
  `?Operation=GetPropertySearchByFolio&clientAppName=PropertySearch&folioNumber=<folio>`.
  Returns a multi-year assessment block per folio. (Geo-sensitive: was intermittently
  unreachable from some networks during research; reachable for the live run.)

### Built
- **`properties/ingest_historical_assessments.py`** (source label `miami_dade_tax_roll`) —
  SEPARATE from the appraiser adapter; the working `ingest.py` was not restructured.
  `fetch_folio()` (network) split from `ingest_assessment_history()` (pure mapping).
  Writes RawSnapshot first; ensures the Parcel spine exists by folio **without** touching
  appraiser-owned descriptors; writes one **dated, append-only** `AssessmentSnapshot` per
  year, idempotent per `(parcel, source, tax_year)`. Reuses `_dec_fit` from `ingest.py`.
- **Field mapping:** `tax_year←Year`, `assessed_value←AssessedValue`,
  `market_value←TotalValue`, `taxable_value←TaxableInfos[year].CountyTaxableValue`,
  `source_record_id="<folio>:<year>"`, `effective_date=Jan 1`. `LandValue`/`BuildingOnlyValue`/
  `ExtraFeatureValue` preserved in RawSnapshot only (no model fields). `annual_tax` left
  null (millage not in this payload).
- **Command** `python manage.py ingest_historical_assessments --zip 33131 [--limit N] | --folio <f>`.
  Joins by folio off already-ingested parcels; refuses county-wide runs.
- **Test-first** (`properties/tests_historical.py`, 4 tests): wrote failing tests
  (module absent) → implemented → green. Assert one dated row per year per parcel (incl. the
  2023 row where assessed≠market), idempotent re-run, no collision with appraiser rows
  (different source), and slice-scale multi-folio idempotency via a stubbed fetch.
- **Cross-adapter guardrail:** extended `_dec_fit` (fit-or-null) to `assessed_value` and
  `sale_price` in the appraiser `ingest.py` too — a bad county money value now yields NULL
  (raw kept) instead of crashing the batch. Full suite: **13 tests passing**.

### Data-quality findings — ZIP 33131 live backfill (200 parcels)
| Metric | Value |
|---|---|
| `miami_dade_tax_roll` AssessmentSnapshot rows | 594 |
| Parcels with ≥1 historical year | 200 / 200 |
| Parcels with full 3 years (2023–2025) | 196 |
| Parcels with 2 years / 1 year | 2 / 2 |
| NULL assessed/market/taxable | 0 / 0 / 0 |
| assessed_value range | $0 – $398,830,669 |

**Live idempotency proof:** re-ran 3 folios → tax_roll rows 594→594 (no dupes), RawSnapshot
+3 (provenance each scrape). Years present: 2023, 2024, 2025 (≈3 prior cycles; the proxy
did not return earlier years for this slice).

**Flagged / suspect — all explained, none are parser bugs:**
- **24 parcels with a $0 assessed year** — all `REFERENCE FOLIO` (admin placeholders) or
  `COMMON AREA` (private park/rec/roadway); legitimately non-taxable. (133 rows have
  `taxable_value=0` for the same reason.)
- **4 parcels with <3 years** — vacant land / parking / reference folios that only appear in
  recent rolls; we ingest whatever years the proxy returns.

## Open decisions for next time
- **Year depth:** this slice returned only 2023–2025 from the proxy (3 cycles). If we want
  5+ years, confirm whether the proxy exposes older rolls via another operation/param, or
  fall back to the Florida DOR NAL files (`floridarevenue.com`, bulk per-year JV/AV/TV).
- **`annual_tax` is null** — the proxy payload carried no millage/tax bill in the block we
  parsed (there is a `MillageList`/non-ad-valorem link we did not ingest). Decide whether to
  compute tax = taxable × millage, or pull the tax-bill endpoint.
- **Value breakdown not modeled** — `LandValue`/`BuildingOnlyValue`/`ExtraFeatureValue` are
  kept only in RawSnapshot. Add model fields if the buyer report needs the land/building split.
- **Backfill is per-folio (one HTTP call each)** — ~1.8s/folio (200 ≈ 5.5 min). Fine for a
  ZIP slice; would need batching/concurrency before any county-scale run.
- **Resolved this session:** prior-year value source (done — PA proxy); `_dec_fit` now covers
  `assessed_value`/`sale_price` across both adapters.
- Still out of scope (by design): other data sources beyond historical assessments, page
  generation, buyer report, scores, entity resolution. Branch not merged/deployed.

## Session 3 (2026-06-12) — Parcel-page template + thin-page guard

On branch `parcel-pages` (not merged; vanthir still has **no git remote**). One template,
validated on one real folio, before any scaling. No mass-generation, no sitemap.

### URL structure
- `path("property/<str:folio>/", ...)` → `/property/<folio>/`, name `parcel_detail`.
  Folio is the spine and the stable unique key, so it's the canonical URL (no address slug
  to drift/collide). `vanthir/urls.py` now `include("properties.urls")`. `<link rel=canonical>`
  points at the same folio URL.

### What the page displays (all from that parcel's own rows; no entity resolution)
- Header: prettified address (ALL-CAPS county string → title case, directionals kept
  upper), city/state/zip, folio + county.
- **Owner of record:** raw `OwnershipSnapshot.owner_name_raw` verbatim, labeled "not resolved
  to a legal entity." Sourced + dated.
- **Characteristics:** year built / living sqft / land use (+ beds/baths only if > 0, so
  commercial parcels don't show "0 beds").
- **Sale history:** table from `Deed` rows (recorded date, price, raw grantee).
- **Assessed-value trajectory (the differentiated content):** CSS bar chart + table of the
  3 tax-roll years (assessed / market / taxable) with a computed net-% change.
- **Flood risk:** from `FloodZoneSnapshot` if present; else honest "not ingested yet."
- Every section carries a `Source: … · date` line (appraiser vs tax roll).

### Computed FAQ + JSON-LD
- `properties/parcel_page.py::build_parcel_page()` computes 4 parcel-specific Q&As (owner /
  value trajectory / last sale / flood) as plain strings, reused **verbatim** in both the
  visible FAQ block and a `FAQPage` JSON-LD `<script>`, so structured data always matches the
  page. JSON-LD is angle-bracket-escaped (no `</script>` breakout).

### Thin-page guard
- `Parcel.is_indexable` (model property, no migration): a parcel is **not** indexable if it
  has no address, OR land use contains `REFERENCE FOLIO` / `COMMON AREA`, OR it has no
  positive assessed value. Non-indexable pages render `<meta robots noindex,follow>`, drop the
  FAQ JSON-LD entirely, and show a "not indexed" badge + footer note. (Chose noindex over 404
  so a human with the link can still view it.)

### Rendered samples (from the live local DB)
- **Good — `0101110601160` / 41 E Flagler St** (HTTP 200, indexable, no robots meta): owner
  *41 FLAGLER REALTY LLC*; retail, 60,426 sqft, built 1928; sold May 26 2021 for $27,200,000;
  trajectory $11,428,750 (2023) → $12,571,625 (2024) → $11,211,250 (2025), net **−1.9%**; flood
  "not ingested yet." 4 FAQ Q&As emitted as valid FAQPage JSON-LD.
- **Junk — `0102090901321`** (reference folio, no address, $0 all years): renders with
  `noindex,follow`, "not indexed" badge, and **no** JSON-LD — thin-guard working.
- Tests: `properties/tests_pages.py` (5) cover indexable render, JSON-LD validity/specificity,
  and 3 thin-guard cases (reference folio, common-area-with-address, address-but-no-value).
  Full suite **18 passing**.

### Note on PR step
A create-PR command fired this session targeting the **conductatlas** repo — but all this work
is in **vanthir**, which has no GitHub remote, and conductatlas is unrelated. No PR was
created; work committed locally on `parcel-pages` only. Decide later if/where vanthir should be
pushed.

## Open decisions for next time
- **Scaling beyond one page** (explicitly deferred): routing/sitemap that emits ONLY indexable
  parcels, address-based slugs if wanted, list/index pages.
- **Address prettifier** is heuristic (`16 SE 2 ST` → `16 SE 2 St`); fine for now, revisit if
  odd casings appear at scale.
- Carried over from Session 2: year depth (3 cycles only), `annual_tax` null, value breakdown
  not modeled, per-folio backfill speed.

## Session 4 (2026-06-12) — Sitemap for indexable parcel pages

On branch `parcel-sitemap` (not merged; vanthir still has **no git remote**, not deployed).
Sitemap only — no new data, no new page types. Scales to what's in the DB now (ZIP 33131)
and auto-grows as more ZIPs are ingested.

### Indexable page set (Step 1, of 200 parcels in 33131)
| | count |
|---|---|
| Total parcels | 200 |
| **Indexable (in sitemap)** | **164** |
| Excluded | 36 |
| — no address | 25 |
| — reference folio | 7 |
| — common area | 4 |
| — no positive $ value | 0 (already caught by the above) |

82% indexable — sane (the 25 no-address matches the Session-1 vacant/govt finding). Not 0,
not all-200, so the guard is applied.

### Mechanism
- **Django sitemaps framework** (`django.contrib.sitemaps` added to INSTALLED_APPS; no DB
  migration). `properties/sitemaps.py::ParcelSitemap`: `items()` = `Parcel.objects.indexable()`
  ordered by folio; `location()` = `reverse("parcel_detail", folio)`; `lastmod()` =
  `parcel.updated_at`; `protocol="https"`, `changefreq="monthly"`, `priority=0.6`.
- **No hardcoded list** — driven by a new `Parcel.objects.indexable()` queryset (set-level twin
  of `Parcel.is_indexable`): excludes blank/whitespace address, `REFERENCE FOLIO`/`COMMON AREA`
  land use, and parcels with no `assessed_value > 0`. A test pins this queryset to the
  per-object guard so they can't drift.
- **No `django.contrib.sites`** — the sitemap view derives the host from the request
  (RequestSite), so URLs are correct locally and in prod with no hardcoded domain.
- **Serves at `/sitemap.xml`** (wired in `vanthir/urls.py`).

### Validation (Step 3, all green)
- `/sitemap.xml` → HTTP 200, `application/xml`, well-formed (parsed with minidom).
- Exactly **164** `<loc>` URLs = `indexable()` count; all `https://…/property/<folio>/`.
- Junk folios absent: `0102090901321` (reference), `0101000000270` (no-addr govt),
  `0101140301150` (common area). Good folio `0101110601160` (41 E Flagler St) present.
- Spot-checked sitemap URLs render 200 and are **not** noindex.
- `properties/tests_sitemap.py` (6 tests) covers all of the above. Full suite: **24 passing**.

### What remains (next sessions)
1. **Deploy Vanthir to Railway** — it isn't deployed yet, so the pages + `/sitemap.xml` aren't
   publicly crawlable. Railway config (`Procfile`, `railway.json`, `runtime.txt`) already
   exists; needs a `DATABASE_URL` (Postgres) and the 33131 data ingested in that environment.
2. **Submit `/sitemap.xml` to Google Search Console** once the domain is live.
3. Then scale ingestion to more ZIPs — pages and sitemap grow automatically (no code change).

## Session 5 (2026-06-12) — First deployment to Railway (LIVE)

Vanthir is deployed and serving real data.

### Where it lives
- **GitHub:** `github.com/leshane-stack/vanthir`, deploy branch `main` (SSH remote `origin`).
- **Railway:** workspace `leshane-stack` › project **`determined-hope`** › service **`vanthir`**,
  environment `production`, region US West. Postgres added as a second service in the same project.
- **Live URL:** **https://vanthir-production.up.railway.app**
- **Env vars (vanthir service):** `SECRET_KEY` (generated), `DEBUG=0`, `DATABASE_URL=${{Postgres.DATABASE_URL}}`.
  `settings.py` auto-adds `RAILWAY_PUBLIC_DOMAIN` to ALLOWED_HOSTS/CSRF.

### Production data (ZIP 33131) — matches local exactly
| | count |
|---|---|
| parcels | 200 |
| indexable (in sitemap) | 164 |
| ownership snapshots | 198 |
| deeds | 134 |
| tax_roll assessments (2023–2025) | 594 |
| tax_roll RawSnapshots | 206 |

### Deploy mechanics / fixes made this session
- **Migrations** run via railway.json `deploy.preDeployCommand` (runtime). Build = `collectstatic` only.
- **Three real bugs surfaced by the first prod deploy (all fixed + committed):**
  1. **NUL in payloads** — PA-proxy payloads contain ` `; Postgres rejects it in JSON (SQLite didn't).
     Added `_strip_nul()` to both adapters (`+ regression test`). Without this, a direct prod historical
     ingest would crash.
  2. **Build-time migrate** — Procfile `release:` ran migrate during *build*, where `postgres.railway.internal`
     doesn't resolve → build failed. Removed it; migrate now runtime-only via `preDeployCommand`.
  3. **Healthcheck under DEBUG=0** — `SECURE_SSL_REDIRECT` 301'd `/healthz`, failing the healthcheck.
     Added `SECURE_REDIRECT_EXEMPT = [r"^healthz$"]`.
- Full local suite still green: **25 tests**.

### How the data got into prod (important, honest note)
The appraiser ingest ran directly against prod (ArcGIS reachable). The **historical backfill could not** —
`apps.miamidadepa.gov` (PA proxy) was geo-unreachable from the deploy environment at ingest time (it's
intermittent; worked in Session 2). So the **594 historical rows were transferred from the validated local
DB** (Session 2's ingest), with provenance, rather than re-scraped in prod. Same real county data (2023–2025
finalized values); only the `observed_at` reflects the Session-2 scrape. A future prod-side backfill (from a
US network) would reproduce identical values — the `_strip_nul` fix now makes that safe on Postgres.

### Live verification (all pass)
- `/sitemap.xml` → 200, 164 https URLs, 41 E Flagler present, junk folio absent.
- `/property/0101110601160/` (41 E Flagler St) → 200, not noindex, owner + $27,200,000 sale +
  $11,428,750→ trajectory + FAQPage JSON-LD.
- reference folio `/property/0102090901321/` → 200, `noindex`.
- `DEBUG=0` confirmed (plain 404, no debug page).

### What remains
- **GitHub auto-deploy isn't firing on push** — git pushes did not trigger deploys; I deployed with
  `railway up` (which may have switched the service's source to CLI-uploaded). To restore push-to-deploy,
  reconnect the repo in the Railway dashboard (Service → Settings → Connect Repo); otherwise deploy via
  `railway up` after pushing.
- **Delete the accidental empty project `laudable-education`** (created during setup, unused).
- **Point a custom domain** (e.g. vanthir.com) in Railway if desired (settings already trust `RAILWAY_PUBLIC_DOMAIN`;
  add the custom host to `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` env or rely on the domain var).
- **Submit `/sitemap.xml` to Google Search Console** — your manual step now that the site is live.
- **Scale ingestion to more ZIPs** — pages and sitemap auto-grow with no code change (ideally run the
  historical backfill from a US network so it scrapes prod directly).

## Session 6 (2026-06-12) — Custom domain (vanthir.com) host/CSRF config

On branch `custom-domain` (not pushed/deployed). Pointing `vanthir.com` + `www.vanthir.com`
at the Railway app via Cloudflare; Django needs to accept the new hosts.

- **Case A** (env-driven): `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` read from env vars in
  `settings.py`, and `RAILWAY_PUBLIC_DOMAIN` is always appended (Railway host preserved).
- **Code change:** updated the hardcoded fallback defaults to be self-documenting and to drop
  the insecure `"*"` default:
  - `ALLOWED_HOSTS` default → `vanthir.com,www.vanthir.com,localhost,127.0.0.1`
  - `CSRF_TRUSTED_ORIGINS` default → `https://vanthir.com,https://www.vanthir.com`
  Env vars still override; `RAILWAY_PUBLIC_DOMAIN` still appended. Local dev keeps
  `localhost`/`127.0.0.1`; tests unaffected (runner auto-adds `testserver`). `check` clean,
  **25 tests passing**.
- **Railway env vars to set (authoritative, recommended)** on the `vanthir` service:
  - `ALLOWED_HOSTS = vanthir.com,www.vanthir.com`
  - `CSRF_TRUSTED_ORIGINS = https://vanthir.com,https://www.vanthir.com`
  (Railway domain auto-appended both places — don't add it manually.)
- **Takes effect on next deploy** (`railway up`) and/or when the env vars are set (setting a
  Railway var redeploys). Also requires the custom domain added in Railway (Service → Settings →
  Networking → Custom Domain) and Cloudflare CNAME → the Railway target, DNS-only or proxied.

## Machine note
If a Django command ever fails with `No module named 'config'`, run
`unset DJANGO_SETTINGS_MODULE` and retry (stale env var). Didn't recur this session.
