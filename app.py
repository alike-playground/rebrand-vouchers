"""Alike Voucher Builder — Streamlit UI.

Ops flow:
  1. Upload TravClan PDF
  2. OCR runs, pre-fills a data model
  3. Ops verifies + edits the pre-filled fields, adds Infinity Booking ID,
     Travel Advisor name, and On-ground contact name+phone
  4. Click Generate → compliance scan runs → download final Alike PDF

Design decisions:
  - No API/AI calls; entirely offline (Tesseract + WeasyPrint locally)
  - JSON editor for the itinerary is intentional — the day-by-day
    structure is too irregular for a fixed form. Top-level fields, ops
    contacts, and hotels get first-class widgets.
  - Every render passes compliance.py before the download button appears.
"""
from __future__ import annotations
import io, json, tempfile, pathlib, traceback
import streamlit as st

from extractor  import extract
from renderer   import render_voucher
from compliance import scan as compliance_scan


st.set_page_config(page_title="Rebrand Vouchers · Alike", page_icon="✈", layout="wide")

# --- header ------------------------------------------------------------

# aliketools header + ToolGuide styling (see hub/BRANDING.md). The FAB is a
# real st.button pinned bottom-right via its st-key-* class (Streamlit >=1.39).
# NOTE: this branding block gets wiped whenever app.py is replaced via GitHub
# web upload from an old base — re-apply it (see CLAUDE.md "Branding").
_ALIKE_LOGO = """<svg class="at-logo" role="img" aria-label="Alike" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 111 40"><path fill="#fdaa63" d="m95.94 0-.02.15-.01.04-.01.12v.06l-.02.1v.08l-.01.04a6 6 0 0 0 .15 1.57 4 4 0 0 0 .6 1.19l.24.3q.06.04.11.12l.06.06.08.08.05.05.07.07.07.06.07.06.06.05.08.07.05.04.1.08.04.04.1.07.08.06.11.08.05.02.1.07.07.03.1.06.06.03.1.06.06.02.1.06.06.02.1.05.07.03.17.07-.26-.05a5 5 0 0 1-1.19-.46l-.1-.06-.45-.3-.05-.04-.04-.03-.06-.04-.02-.02-.06-.06-.1-.1-.08-.07-.04-.05-.03-.03-.04-.05-.03-.02-.05-.06q-.13-.13-.23-.28l-.02-.03-.1-.14-.18-.29q.09.71.44 1.33.42.71 1.1 1.23.69.5 1.5.85.87.38 1.75.7.86.32 1.67.72c.7.35 1.36.8 1.84 1.38l.1-.06.44-.18q0-.04-.02-.07v-.04l-.03-.08-.01-.04q0-.05-.03-.08v-.03l-.05-.11-.12-.26-.06-.12a5 5 0 0 0-.36-.6l-.28-.4-.31-.39a10 10 0 0 0-.96-.97l-.14-.13-.29-.24-.14-.12-.3-.23-.3-.23-.93-.66q-.82-.6-1.65-1.18c-.58-.4-1.18-.8-1.69-1.28l-.24-.25-.12-.13-.1-.14-.1-.14A3 3 0 0 1 95.93 0m10.24 4.11q-.55.01-1.03.25a2 2 0 0 0-.89.85q-.2.38-.22.8-.04.37 0 .73.16-.46.46-.86.28-.42.73-.68.3-.15.64-.2.18-.05.38-.05a1.2 1.2 0 0 0-1.16.13 1 1 0 0 1 .37-.34 1 1 0 0 1 .51-.1q.26.01.48.15.18.09.33.24-.57.31-.92.86a4 4 0 0 0-.5 1.95q0 .48-.03.95a3 3 0 0 1 1.32.07l.1-.53.12-.72.04-.19q.07-.4.24-.8a2 2 0 0 1 .52-.68l.16-.14.18-.14.24-.16.03-.01.11-.07.05-.03.58-.3.2-.1.03-.01.07-.03.12-.05.16-.07.2-.08a3.6 3.6 0 0 0-2.02-.01 2.2 2.2 0 0 0-1.34-.62zm-8.86 1.9.52.73.03.04.16.2.05.06.07.08.09.1.08.11.1.13.05.05.15.18.24.26.08.08q.15.17.33.32l.1.09.1.08.43.32.11.08.12.07q.1.08.2.12.15.1.32.18l.22.12.33.15.46.19.23.08q-.14.38-.34.71l-.07.12-.14.2a14 14 0 0 1 .9.86l.22.24.22.26q-.03-.37.01-.73.05-.57.32-1.12.22-.42.57-.75a3 3 0 0 0-.43-.46q-.85-.75-1.87-1.26c-.65-.33-1.33-.58-2-.87A24 24 0 0 1 97.32 6"/><path fill="#ff7f41" d="M92.89 3.95a7 7 0 0 0 .1 2.72q.25.81.78 1.5a7 7 0 0 0 3.01 2.27 5.4 5.4 0 0 1-3.94-2.74q.1.88.55 1.66.53.9 1.38 1.55.88.65 1.9 1.07 1.08.47 2.2.89 1.08.4 2.1.9a7 7 0 0 1 2.38 1.82q.44.57.68 1.24.09-.45.05-.92a4 4 0 0 0-.3-1.26 7 7 0 0 0-.67-1.23q-.7-1.03-1.6-1.9c-.92-.87-1.97-1.6-3-2.34l-2.1-1.48c-.98-.68-2-1.35-2.72-2.27a5 5 0 0 1-.81-1.48m12.9 5.18a3 3 0 0 0-1.3.31 2.5 2.5 0 0 0-1.12 1.07q-.23.47-.28 1-.04.46 0 .93.22-.59.58-1.1c.25-.33.55-.64.94-.84q.36-.2.8-.27.23-.05.48-.06-.3-.1-.6-.11-.48 0-.87.29.16-.29.47-.44t.64-.12.6.19q.24.12.42.3-.72.4-1.16 1.08a5 5 0 0 0-.63 2.46c-.02.52 0 1.03-.03 1.55a6 6 0 0 1-.5 2.23 7 7 0 0 1-2.64 2.95q-.66.4-1.4.65a8 8 0 0 0 3.74-1.57 6 6 0 0 0 2.28-3.7q.18-.75.3-1.48l.2-1.15q.1-.53.31-1 .22-.44.55-.78.39-.37.87-.66.59-.35 1.22-.63l.69-.3a4.5 4.5 0 0 0-2.55-.01 2.8 2.8 0 0 0-1.7-.78zm-11.16 2.39a20 20 0 0 0 2.45 2.94q.5.45 1.08.8 1.05.66 2.22 1.06a5.4 5.4 0 0 1-1.49 2.18 7 7 0 0 1-2.62 1.41q-1.62.52-3.34.44c.9.68 1.98 1.14 3.12 1.3a5.6 5.6 0 0 0 3.14-.35c.68-.3 1.28-.75 1.82-1.26a7 7 0 0 0 1.56-2.07q.17-.36.23-.74a2 2 0 0 0-.07-.77c-.14-.4-.44-.7-.76-1a11 11 0 0 0-2.37-1.58c-.81-.41-1.67-.74-2.51-1.1q-1.26-.56-2.46-1.26"/><path fill="url(#a)" d="M107.63 10.35q-.29.1-.55.27c-.44.27-.9.6-1.18 1.03-.46.72-.63 1.6-.67 2.44-.02.52 0 1.04-.03 1.56a6 6 0 0 1-.5 2.23 6 6 0 0 1-1.21 1.75q-.44.47-.95.86.74-.35 1.37-.85a6 6 0 0 0 2.28-3.7q.18-.74.3-1.48l.2-1.15q.1-.53.31-1 .22-.44.55-.78.39-.37.87-.66.59-.35 1.22-.63l.43-.2a4 4 0 0 0-.96 0q-.44.02-.9.13-.28.07-.58.18"/><path fill="url(#b)" d="M95.17 12.27q.51.7 1.1 1.36.38.44.81.84.5.45 1.08.8 1.05.66 2.22 1.06a5.4 5.4 0 0 1-1.49 2.17 7 7 0 0 1-2.62 1.42q-1.62.5-3.33.44a7 7 0 0 0 3.68 1.37 5.3 5.3 0 0 0 3.54-1.67 7 7 0 0 0 1.56-2.07q.17-.36.23-.75a2 2 0 0 0-.07-.77c-.14-.38-.44-.7-.76-.98q-1.07-.94-2.37-1.6c-.82-.4-1.67-.73-2.51-1.1z"/><path fill="url(#c)" d="M92.89 3.95q-.1.57-.1 1.15a4.5 4.5 0 0 0 .94 2.38 7 7 0 0 0 3.01 2.28 6 6 0 0 1-2.67-1.23c.72.84 1.65 1.5 2.7 1.9a6 6 0 0 1-2.36-.98 5 5 0 0 1-1.35-1.4q.12.33.3.63c.33.6.82 1.12 1.38 1.55q.87.64 1.9 1.07 1.08.47 2.2.88 1.06.4 2.1.91a7 7 0 0 1 2.38 1.81q.44.57.67 1.25.06-.27.07-.54a4 4 0 0 0-.28-.97 7 7 0 0 0-.67-1.24q-.7-1.02-1.6-1.89c-.92-.87-1.97-1.6-3-2.34l-2.1-1.47c-.98-.68-2-1.35-2.72-2.27a5 5 0 0 1-.81-1.47"/><path fill="url(#d)" d="M42.6 15.35a3 3 0 0 1-2.13-.82 2.7 2.7 0 0 1-.86-2.01q0-1.2.86-2.02a3 3 0 0 1 2.13-.81q1.24 0 2.1.81t.87 2.02-.87 2.01q-.86.82-2.1.82"/><path fill="#1a1a1a" d="M29.4 10.85V35.2q0 1.8 1.34 3.08 1.36 1.27 3.27 1.27h.07V15.21q0-1.78-1.35-3.05a4.6 4.6 0 0 0-3.33-1.3m21.65 0V35.2q0 1.8 1.35 3.08 1.35 1.27 3.27 1.27h.06V29.5l2.03 2.27L63.3 38a4.5 4.5 0 0 0 3.1 1.52 4.6 4.6 0 0 0 3.45-1.03L60.8 28.34l9.05-10.15a4.6 4.6 0 0 0-3.44-1.03 4.5 4.5 0 0 0-3.11 1.52l-5.55 6.23-2.03 2.27V15.2q0-1.78-1.34-3.05a4.6 4.6 0 0 0-3.34-1.3m-40.37 6.98q-2.96 0-5.42 1.4-2.42 1.35-3.86 3.83a11.4 11.4 0 0 0-1.4 5.7Q0 32 1.4 34.54q1.43 2.52 3.86 3.95 2.42 1.4 5.34 1.4 2.62 0 4.68-1a10 10 0 0 0 3.38-2.65v-.01q.3 1.16 1.26 2.04 1.35 1.27 3.26 1.27h.07v-17q0-1.78-1.35-3.05a4.7 4.7 0 0 0-3.37-1.3v3.1a9 9 0 0 0-3.2-2.45 10 10 0 0 0-4.65-1.01m72.88 0q-3.33 0-5.92 1.36-2.55 1.36-3.98 3.87a11.6 11.6 0 0 0-1.4 5.78q0 3.3 1.44 5.82a10.4 10.4 0 0 0 4.03 3.87q2.58 1.36 5.83 1.36 3.99 0 6.65-1.86a10 10 0 0 0 3.65-4.6l-.43-.24a4 4 0 0 0-1.79-.46q-1.27 0-2.41.98c-1.61 1.42-3.57 2.46-5.68 2.46a6.6 6.6 0 0 1-4.43-1.55 5.8 5.8 0 0 1-2.01-4.1h17.28q.17-.97.17-2.18 0-3.1-1.4-5.46a9.5 9.5 0 0 0-3.9-3.72 12 12 0 0 0-5.7-1.33m-43.34.35v17q0 1.8 1.35 3.09 1.35 1.27 3.27 1.27h.07v-17q0-1.78-1.35-3.06a4.6 4.6 0 0 0-3.34-1.3m43.13 3.37q2.67 0 4.48 1.47a5 5 0 0 1 1.85 3.91H77.15q.33-2.48 2.06-3.91a6.2 6.2 0 0 1 4.14-1.47m-71.69.12q1.8 0 3.37.85a6.5 6.5 0 0 1 2.5 2.48q.99 1.63.99 3.83v.02q0 2.2-.99 3.87a6.4 6.4 0 0 1-2.5 2.48 7.1 7.1 0 0 1-6.73 0 7 7 0 0 1-2.55-2.56 8 8 0 0 1-.94-3.88q0-2.2.94-3.8a6.5 6.5 0 0 1 2.55-2.44 7 7 0 0 1 3.36-.85"/><defs><linearGradient id="a" x1="102.27" x2="111.98" y1="11.77" y2="18.53" gradientUnits="userSpaceOnUse"><stop stop-color="#ff7f41"/><stop offset="1" stop-color="#f8485e"/></linearGradient><linearGradient id="b" x1="92.6" x2="101.92" y1="13.86" y2="22.48" gradientUnits="userSpaceOnUse"><stop stop-color="#ff7f41"/><stop offset="1" stop-color="#f8485e"/></linearGradient><linearGradient id="c" x1="92.38" x2="104.36" y1="5.99" y2="16.71" gradientUnits="userSpaceOnUse"><stop stop-color="#ff7f41"/><stop offset="1" stop-color="#f8485e"/></linearGradient><linearGradient id="d" x1="39.39" x2="44.98" y1="10.64" y2="16.34" gradientUnits="userSpaceOnUse"><stop stop-color="#ff7f41"/><stop offset="1" stop-color="#f8485e"/></linearGradient></defs></svg>"""

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600&family=DM+Sans:wght@400;500;600;700&display=swap');
.at-header{background:#fff;border:1px solid #e9e4dc;border-radius:10px;padding:14px 18px;display:flex;align-items:center;gap:14px;margin-bottom:8px;font-family:'DM Sans',sans-serif}
.at-header .at-logo{height:26px;width:72px;display:block}
.at-header .at-tt{border-left:1px solid #e9e4dc;padding-left:14px}
.at-header .at-tool{font-family:'Cormorant Garamond',serif;font-size:22px;font-weight:500;color:#171512;line-height:1.1;margin:0}
.at-header .at-sub{font-size:12px;color:#5b5347;margin:3px 0 0}
.st-key-tg_fab{position:fixed;right:22px;bottom:22px;z-index:999;width:auto}
.st-key-tg_fab button{border-radius:999px;border:1px solid #e9e4dc;background:#fff;color:#3a362f;font-weight:600;font-size:13px;padding:6px 16px;box-shadow:0 2px 10px rgba(23,21,18,.08)}
.st-key-tg_fab button:hover{border-color:#ff7f41;color:#171512}
</style>
<div class="at-header">
  <a href="https://aliketools.dev" aria-label="aliketools home">""" + _ALIKE_LOGO + """</a>
  <div class="at-tt">
    <p class="at-tool">Rebrand Vouchers</p>
    <p class="at-sub">TravClan B2B voucher → Alike-branded B2C voucher · OCR-assisted, fully offline</p>
  </div>
</div>
""", unsafe_allow_html=True)


# --- ToolGuide (same UX as the other aliketools tools; once per session) ---

@st.dialog("How to use — Rebrand Vouchers")
def _show_guide():
    st.markdown(
        """
**Turns a TravClan B2B voucher into an Alike-branded B2C voucher.** Fully
offline — OCR runs on the server, nothing is sent anywhere else, and every
voucher passes a compliance scan before it can be downloaded.

1. **Upload** the TravClan voucher PDF — OCR pre-fills the fields (give it a few seconds).
2. **Verify everything** — guest, hotels, dates. OCR is good but not perfect; you are the checker.
3. **Add what OCR can't know** — the **Infinity Booking ID**, the travel advisor's name, and the on-ground contact.
4. **The itinerary is raw JSON on purpose** (day-by-day structures are too irregular for a form) — edit carefully and keep the quotes balanced.
5. **Generate** — the compliance scan must **PASS** (no TravClan traces, Alike contacts present) before the download button unlocks.

*Tip: if compliance fails, the findings name the exact forbidden text — fix that field and generate again.*
"""
    )


# ToolGuide triggers — MUST live above the upload flow's st.stop() early-exit,
# or they never run on a fresh page. The FAB is position:fixed, so its DOM
# position here doesn't affect where it appears.
if st.button("? How to use", key="tg_fab"):
    _show_guide()

if not st.session_state.get("_guide_shown"):
    st.session_state["_guide_shown"] = True
    _show_guide()


# --- session state -----------------------------------------------------

if "data" not in st.session_state:
    st.session_state["data"] = None
if "warnings" not in st.session_state:
    st.session_state["warnings"] = []
if "pdf_bytes" not in st.session_state:
    st.session_state["pdf_bytes"] = None
if "compliance" not in st.session_state:
    st.session_state["compliance"] = (None, [])


if "ocr_raw" not in st.session_state:
    st.session_state["ocr_raw"] = []


# --- step 1: upload ----------------------------------------------------

with st.container(border=True):
    st.subheader("1. Upload the TravClan voucher PDF")
    uploaded = st.file_uploader("Drop the source voucher here", type=["pdf"],
                                label_visibility="collapsed")
    col1, col2 = st.columns([1, 3])
    with col1:
        run_ocr = st.button("Run OCR", type="primary", disabled=(uploaded is None),
                            use_container_width=True)
    with col2:
        if uploaded:
            st.caption(f"Ready: **{uploaded.name}** ({uploaded.size/1024:.0f} KB)")

    if run_ocr and uploaded:
        with st.spinner("OCR-ing pages at 300 DPI, extracting thumbnails, and parsing…"):
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                tf.write(uploaded.read())
                tmp_path = tf.name
            # Persistent thumb dir so the renderer can reference the paths later
            thumb_dir = tempfile.mkdtemp(prefix="alike_thumbs_")
            st.session_state["thumb_dir"] = thumb_dir
            try:
                data = extract(tmp_path, thumb_dir=thumb_dir)
                st.session_state["warnings"] = data.pop("_ocr_warnings", [])
                st.session_state["ocr_raw"]  = data.pop("_ocr_pages_raw", [])
                st.session_state["vendor_booking_id"] = data.pop("_vendor_booking_id", "")
                st.session_state["data"] = data
                st.session_state["pdf_bytes"] = None
                # Alert if extraction is essentially empty
                t = data["trip"]
                empty = (not t.get("booking_id") and not t.get("hotels")
                         and not t.get("days") and not t.get("arrival"))
                total_thumbs = sum(len(s.get("thumbs", []))
                                   for d in t.get("days", [])
                                   for s in d.get("stops", []))
                if empty:
                    st.error("OCR ran, but no TravClan voucher fields were detected. "
                             "This may not be a standard TravClan booking voucher. "
                             "Expand **Show raw OCR text** below to see what OCR captured, "
                             "and share the source PDF with the tool owner to teach the "
                             "parser this format.")
                else:
                    st.success(f"OCR complete — {len(t.get('days',[]))} days, "
                               f"{len(t.get('hotels',[]))} hotels, "
                               f"{total_thumbs} thumbnails extracted. Verify the fields below.")
            except Exception as e:
                st.error(f"OCR failed: {e}")
                st.code(traceback.format_exc())
            finally:
                pathlib.Path(tmp_path).unlink(missing_ok=True)


data = st.session_state["data"]
if not data:
    st.info("Upload a TravClan PDF and click **Run OCR** to begin.")
    st.stop()

# Warnings
if st.session_state["warnings"]:
    with st.container(border=True):
        st.warning("OCR left these fields for you to confirm:")
        for w in st.session_state["warnings"]:
            st.write(f"• {w}")

# Raw OCR debug — always available after a run
if st.session_state.get("ocr_raw"):
    with st.expander("🔍 Show raw OCR text (per page)", expanded=False):
        st.caption("This is what Tesseract read off the pages. If your fields "
                   "aren't populated above, check whether the text is here — if "
                   "yes, the parser needs to learn this voucher format; if no, "
                   "the source PDF may be scanned poorly or in a language OCR "
                   "isn't tuned for.")
        for i, page_text in enumerate(st.session_state["ocr_raw"], 1):
            st.markdown(f"**Page {i}**")
            st.code(page_text or "(no text detected)", language=None)


# --- step 2: verify + edit ---------------------------------------------

trip = data["trip"]
contacts = data["contacts"]

st.subheader("2. Verify and edit")

# Top-level trip meta
with st.container(border=True):
    st.markdown("**Booking**")
    c1, c2, c3 = st.columns(3)
    trip["booking_id"] = c1.text_input("Booking ID *(paste Infinity Order ID here)*",
                                       value=trip.get("booking_id", ""),
                                       help="Ops enters the Infinity Order ID; label on the voucher stays 'Booking ID'.")
    vendor_bid = st.session_state.get("vendor_booking_id", "")
    if vendor_bid and trip["booking_id"].strip().lower() == vendor_bid.strip().lower():
        c1.warning(f"⚠ This is still the vendor's booking ID (`{vendor_bid}`). "
                   f"Replace with the Infinity Order ID before generating.")
    trip["destination"] = c2.text_input("Destination", value=trip.get("destination", ""))
    trip["nights"]      = c3.number_input("Nights", min_value=0, max_value=60,
                                          value=int(trip.get("nights") or 0))
    c4, c5, c6 = st.columns(3)
    trip["travel_date_display"] = c4.text_input("Travel Date (display)", value=trip.get("travel_date_display", ""))
    trip["pax_display"]         = c5.text_input("Pax (display)", value=trip.get("pax_display", ""))
    trip["guest_lead"]          = c6.text_input("Guest Lead (title case)", value=trip.get("guest_lead", ""))
    trip["guest_lead_first"]    = trip["guest_lead"].split()[0] if trip["guest_lead"] else ""

    st.markdown("**Guests**")
    guests_str = st.text_area("One guest per line",
                              value="\n".join(trip.get("guests", [])), height=100)
    trip["guests"] = [g.strip() for g in guests_str.split("\n") if g.strip()]


# Contacts
with st.container(border=True):
    st.markdown("**On-Ground & Contacts**")
    c1, c2 = st.columns(2)
    contacts["advisor"]         = c1.text_input("Travel Advisor (name of the person who sold the package)",
                                                value=contacts.get("advisor", ""))
    contacts["emergency"]       = c2.text_input("24×7 Emergency Number",
                                                value=contacts.get("emergency", "+91 95133 92429"))
    c3, c4, c5 = st.columns(3)
    contacts["on_ground_name"]  = c3.text_input("On-ground Support (name)",
                                                value=contacts.get("on_ground_name", ""))
    contacts["on_ground_phone"] = c4.text_input("On-ground Support (phone)",
                                                value=contacts.get("on_ground_phone", ""))
    contacts["careline"]        = c5.text_input("Careline (WhatsApp)",
                                                value=contacts.get("careline", "+91 88000 25030"))


# Arrival / Departure
with st.container(border=True):
    st.markdown("**Arrival & Departure**")

    def _flight_editor(label: str, key: str):
        val = trip.get(key) or {}
        c1, c2, c3, c4 = st.columns([1, 2, 1, 3])
        mode    = c1.text_input(f"{label} Mode", value=val.get("mode", "Flight"), key=f"{key}_m")
        date    = c2.text_input(f"{label} Date", value=val.get("date", ""), key=f"{key}_d")
        time    = c3.text_input(f"{label} Time", value=val.get("time", ""), key=f"{key}_t")
        remarks = c4.text_input(f"{label} Remarks", value=val.get("remarks") or "", key=f"{key}_r")
        if date or time or remarks:
            trip[key] = {"mode": mode, "date": date, "time": time, "remarks": remarks}
        else:
            trip[key] = None

    _flight_editor("Arrival", "arrival")
    _flight_editor("Departure", "departure")


# Hotels
with st.container(border=True):
    st.markdown("**Hotels**")
    hotels = trip.get("hotels", [])
    add, _, rm = st.columns([1, 4, 1])
    if add.button("+ Add hotel"):
        hotels.append({"name": "", "location": "", "check_in": "", "check_out": ""})
    if rm.button("− Remove last") and hotels:
        hotels.pop()

    new_hotels = []
    for i, h in enumerate(hotels):
        with st.expander(f"{i+1}. {h.get('name') or '(new hotel)'}", expanded=False):
            c1, c2 = st.columns([3, 2])
            h["name"]     = c1.text_input("Hotel name", value=h.get("name", ""), key=f"h_{i}_n")
            h["location"] = c2.text_input("Location", value=h.get("location", ""), key=f"h_{i}_l")
            c3, c4, c5 = st.columns(3)
            h["check_in"]  = c3.text_input("Check-in",  value=h.get("check_in",  ""), key=f"h_{i}_ci")
            h["check_out"] = c4.text_input("Check-out", value=h.get("check_out", ""), key=f"h_{i}_co")
            h["stars"] = c5.number_input("Stars (0=hide)", 0, 5,
                                         value=int(h.get("stars") or 0), key=f"h_{i}_s") or None
            c6, c7, c8 = st.columns(3)
            h["rooms_guests"] = c6.text_input("Rooms & Guests", value=h.get("rooms_guests", ""), key=f"h_{i}_rg")
            h["room_type"]    = c7.text_input("Room Type",      value=h.get("room_type", ""),    key=f"h_{i}_rt")
            h["meal_plan"]    = c8.text_input("Meal Plan",      value=h.get("meal_plan", ""),    key=f"h_{i}_mp")
        new_hotels.append(h)
    trip["hotels"] = [h for h in new_hotels if h.get("name")]


# Days — JSON editor (structure is too irregular for fixed widgets)
with st.container(border=True):
    st.markdown("**Day-by-Day Itinerary** — edit inline as JSON")
    st.caption("Each day is `{n, date_display, location, stops:[{title, pickup, pickup_time, drop, remarks, description}], leisure}`.")
    days_json = st.text_area("Itinerary JSON",
                             value=json.dumps(trip.get("days", []), indent=2),
                             height=380, label_visibility="collapsed")
    try:
        trip["days"] = json.loads(days_json)
        st.caption(f"✓ Valid JSON — {len(trip['days'])} days parsed.")
    except json.JSONDecodeError as e:
        st.error(f"JSON error: {e}")


# Terms
with st.container(border=True):
    st.markdown("**Terms & Conditions** — one bullet per line (kept vendor-neutral)")
    terms_str = st.text_area("T&Cs",
                             value="\n".join(trip.get("terms", [])),
                             height=280, label_visibility="collapsed")
    trip["terms"] = [t.strip() for t in terms_str.split("\n") if t.strip()]


# --- step 3: generate --------------------------------------------------

st.subheader("3. Generate the Alike voucher")

col1, col2 = st.columns([1, 4])
generate = col1.button("Generate voucher PDF", type="primary", use_container_width=True)

if generate:
    with st.spinner("Rendering PDF and running compliance scan…"):
        try:
            out_path = tempfile.mktemp(suffix=".pdf")
            render_voucher(data, out_path)
            ok, findings = compliance_scan(out_path,
                vendor_booking_id=st.session_state.get("vendor_booking_id") or None)
            with open(out_path, "rb") as f:
                st.session_state["pdf_bytes"] = f.read()
            st.session_state["compliance"] = (ok, findings)
            pathlib.Path(out_path).unlink(missing_ok=True)
        except Exception as e:
            st.error(f"Render failed: {e}")
            st.code(traceback.format_exc())


ok, findings = st.session_state["compliance"]
if st.session_state["pdf_bytes"]:
    if ok:
        st.success("✓ Compliance scan PASSED — no vendor identifiers detected.")
    else:
        st.error("✗ Compliance scan FAILED:")
        for f in findings:
            st.write(f)
    # Show warnings even on pass
    warn_only = [f for f in findings if not f.startswith("❌")]
    if warn_only and ok:
        for f in warn_only:
            st.warning(f)

    # Filename
    guest_last = (trip.get("guest_lead", "").split() or ["Guest"])[-1]
    dest_slug  = (trip.get("destination", "Dest") or "Dest").replace(" ", "").replace("/", "-")
    booking    = trip.get("booking_id", "REF")
    fname = f"Alike_{dest_slug}_{booking}_{guest_last}.pdf"

    st.download_button("↓ Download Alike voucher",
                       data=st.session_state["pdf_bytes"],
                       file_name=fname,
                       mime="application/pdf",
                       type="primary",
                       disabled=not ok)
