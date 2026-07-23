# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Rebrand Vouchers** (formerly "Alike Voucher Builder") — an internal ops tool (Streamlit, Python 3.10+) that converts TravClan B2B booking voucher PDFs into Alike-branded B2C voucher PDFs. Git remote: `github.com/alike-playground/rebrand-vouchers` (copied from `Alike-io/alike-voucher`, which is being decommissioned); surfaced as the **Rebrand Vouchers** tile on aliketools, which links out to the live Streamlit Cloud app at https://voucher-creator-alike.streamlit.app/. It is **fully offline by design**: no API calls, no LLM, no external services — a deliberate decision locked with the PM (see README "Design decisions"). It does **not** talk to the shared Magento GraphQL backend (api.alike.io) or any other Alike service; the only ecosystem touchpoint is that ops manually pastes the **Infinity Order ID** (Alike's internal order reference) into the "Booking ID" field, and the output carries Alike contact channels (`care@alike.io`, careline `+91 88000 25030`).

Ops flow: upload TravClan PDF → Tesseract OCR pre-fills a data model → ops verifies/edits in the form and adds Infinity Booking ID + Travel Advisor + on-ground contact → render PDF → compliance scan → download (button disabled if scan fails).

## Commands

```bash
# Install (system deps first: tesseract-ocr + pango/cairo libs for WeasyPrint)
sudo apt install tesseract-ocr libpango-1.0-0 libpangoft2-1.0-0   # Debian/Ubuntu
python -m venv .venv && source .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run the app (opens http://localhost:8501)
streamlit run app.py

# Each pipeline stage also runs standalone as a CLI — useful for testing without the UI:
python extractor.py path/to/travclan.pdf          # OCR + parse → JSON on stdout
python renderer.py samples/jash_data.json out.pdf # data JSON → PDF
python compliance.py out.pdf                      # scan; exit 0 = PASS, 1 = FAIL
```

There is no test suite, linter, or CI in this repo. The standalone CLIs above plus `samples/jash_data.json` (a complete, real-shaped data model) are the de-facto test harness: after changing the renderer or template, run `python renderer.py samples/jash_data.json /tmp/out.pdf && python compliance.py /tmp/out.pdf` and eyeball the PDF.

Deployment is Streamlit Cloud one-click from GitHub: `packages.txt` (apt system deps) + `requirements.txt` at repo root are all it needs. ⚠ The live app (voucher-creator-alike.streamlit.app) still deploys from the *original* `Alike-io/alike-voucher` repo — re-deploy Streamlit Cloud from this repo (or re-platform onto Cloudflare, planned as a later update) **before** the original is deleted, or the aliketools tile goes dead.

## Architecture

Five flat modules, one pipeline:

```
app.py (Streamlit UI, session state, form widgets)
  └─ extractor.py   OCR + regex parsing → data dict
  └─ renderer.py    data dict → Jinja2 (templates/voucher.html) → WeasyPrint PDF
       └─ brand.py  per-destination gradient colors + Alike orange/ink tokens
  └─ compliance.py  scans rendered PDF text (PyMuPDF) for forbidden/required tokens
```

**Data model** (the contract between all modules): a dict `{"trip": {...}, "contacts": {...}}`. `trip` holds `booking_id, destination, nights, travel_date_display, pax_display, guest_lead, guests[], arrival/departure ({mode,date,time,remarks} or None), hotels[], days[], terms[]`. Each day is `{n, date_display, location, stops:[{title, pickup, pickup_time, drop, remarks, description, thumbs[]}], leisure}`. `samples/jash_data.json` is the reference example. `extract()` also returns `_ocr_warnings` and `_ocr_pages_raw` keys that `app.py` pops off before storing.

**extractor.py** — the source TravClan PDFs have *no usable text layer* (Qt-rendered, Identity-H encoding), so every page is rasterized at 300 DPI and OCR'd with Tesseract (`ocr_pdf`), then parsed with per-section regex heuristics (`parse_booking_meta`, `parse_guests`, `parse_flights`, `parse_hotels`, `parse_days`, `parse_terms`). Embedded hotel/activity thumbnails are pulled with PyMuPDF (`extract_embedded_images`, min 200px side to skip decorative icons) and matched to itinerary stops by y-position using Tesseract word bounding boxes (`_match_thumbs_to_stops` — best-effort, failures are swallowed). `_sanity_check` produces the ops-facing warnings. When a new TravClan format breaks parsing, the raw OCR text is exposed in the UI ("Show raw OCR text" expander) so the parser can be taught the format.

**renderer.py** — Jinja2 renders `templates/voucher.html` (single ~430-line file: all CSS inline, A4 `@page` rules, Poppins `@font-face` from `static/fonts/`), then WeasyPrint writes the PDF. **All asset paths must be absolute `file://` URLs** (project SOP — WeasyPrint won't resolve them otherwise); `_file_url()` handles this for logos, fonts, and thumbs.

**brand.py** — `gradient_for(destination)` picks a 3-stop hero gradient by substring match on the destination (bali, vietnam, thailand, dubai, ...; falls back to `default`). Brand constants: `ORANGE = #ec601d`, `INK = #18181b`.

**compliance.py** — the gate before delivery. Extracts text from the rendered PDF and checks:
- FORBIDDEN tokens (hard fail, ❌): `travclan`, `ontrip`/`on trip`, `query code`, `created by`, known TravClan phone `919116037503`, vendor app CTAs, and the `₹` symbol (vouchers must not show INR pricing; USD tipping guidance is allowed).
- REQUIRED tokens (hard fail if missing): `alike`, `care@alike.io`, `88000 25030`, `Booking`.
- Heuristic vendor-slug detector (⚠ warning only): 6-char `t`-prefixed alphanumerics mixing letters and digits, with an English-word whitelist.

`app.py` disables the download button unless the scan passes. If you change voucher content, keep the REQUIRED tokens present and never reintroduce vendor identifiers.

## Non-obvious conventions

- **The itinerary is edited as raw JSON in the UI** — intentional, not a TODO. The day/stop structure is too irregular for fixed widgets; only top-level trip fields, contacts, and hotels get first-class form inputs.
- The voucher label stays "Booking ID" but ops pastes the **Infinity Order ID** into it (free text).
- What's kept vs. dropped from the source voucher is a locked PM decision (README table D3/D4): kept — emergency line, compulsory-tipping T&C, full T&Cs, thumbnails; dropped — OnTrip app banner, Query Code, vendor phones, USD package pricing, TravClan/Confirmation IDs, star icons, liability boilerplate.
- Output filename convention (in `app.py`): `Alike_{DestSlug}_{BookingID}_{GuestLastName}.pdf`.
- `build/` and `*.pdf` are git-ignored, **except** `samples/*.pdf` which are allowed (test fixtures).
- Default contact values live as widget defaults in `app.py` (emergency `+91 95133 92429`, careline `+91 88000 25030`) — the careline also appears in `compliance.py` REQUIRED, so change both together.

## Troubleshooting (from README)

- WeasyPrint "cannot load library" errors → missing pango/gobject system packages (`packages.txt` covers Streamlit Cloud; install equivalents locally).
- Wrong fonts → check `static/fonts/Poppins-*.ttf` are ≥100 KB (a silent partial download leaves ~84-byte stubs).
- OCR speed: ~4–6 s per page at 300 DPI; a 5-page voucher takes ~25 s. Normal.
