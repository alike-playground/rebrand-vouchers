"""OCR-based extractor for TravClan vouchers.

The source PDFs have no usable text layer (Qt-rendered with Identity-H
encoding that breaks reverse-mapping to Unicode). We rasterize each
page at 300 DPI and run Tesseract, then parse structured fields out of
the OCR text with heuristics and regex.

Output shape matches what the renderer expects. Ops verifies and
corrects the pre-filled values in the Streamlit form before rendering.
"""
from __future__ import annotations
import io, re
from typing import Optional, Tuple, List
import fitz
import pytesseract
from PIL import Image


# --- OCR ---------------------------------------------------------------

def ocr_pdf(pdf_path: str, dpi: int = 300) -> List[str]:
    """Return one OCR string per page."""
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        pages.append(pytesseract.image_to_string(img))
    doc.close()
    return pages


def extract_embedded_images(pdf_path: str, out_dir: str, min_side: int = 200) -> List[dict]:
    """Extract embedded activity/hotel thumbnails from the source PDF.

    Returns [{page, index, path, width, height}]. Skips tiny decorative
    images (icons, app-badges) via the min_side threshold — real thumbs
    are usually ≥300px on the short side, decorative icons are ≤150px.
    """
    import os
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    doc = fitz.open(pdf_path)
    for page_num, page in enumerate(doc, 1):
        for img_i, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            if pix.n - pix.alpha > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            if pix.width < min_side or pix.height < min_side:
                pix = None
                continue
            fname = f"p{page_num}_i{img_i}_{pix.width}x{pix.height}.png"
            fpath = os.path.join(out_dir, fname)
            pix.save(fpath)
            saved.append({"page": page_num, "index": img_i,
                          "path": fpath, "width": pix.width, "height": pix.height})
            pix = None
    doc.close()
    return saved


# --- helpers -----------------------------------------------------------

MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
DATE   = rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTHS},?\s*\d{{4}}"
TIME   = r"\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)"


def _first(pattern: str, text: str, flags=re.IGNORECASE) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# --- field parsers -----------------------------------------------------

def parse_booking_meta(text: str) -> dict:
    d = {}
    d["vendor_booking_id"] = _first(r"Booking\s*Id[:\s]+([A-Za-z0-9]+)", text)
    d["vendor_query_code"] = _first(r"Query\s*Code[:\s]+([A-Za-z0-9]+)", text)
    d["travel_date"]       = _first(rf"Travel\s*Date[:\s]+({DATE})", text)
    d["nights"]            = _first(r"No\s*of\s*Nights[:\s]+(\d+)", text)
    d["destination"]       = _first(r"Destination[:\s]+([A-Za-z /]+?)(?:\n|Pax|$)", text)
    d["pax"]               = _first(r"Pax[:\s]+(\d+\s*Adult(?:s)?(?:\s*[|,]\s*\d+\s*Child(?:ren)?)?)", text)

    # Guest lead: matches "MAYANK's Vietnam Trip" (all caps), "Mayank's..."
    # (title case), "Jash Bharat's..." (multi-word title), or
    # "Nilesh's Bali / Indonesia Trip" (destination with slash).
    m = re.search(r"([A-Z][a-zA-Z][a-zA-Z\s]{1,30})'s\s+[A-Za-z][A-Za-z\s/]{1,40}?\s+Trip",
                  text)
    if m:
        d["guest_lead"] = _clean(m.group(1)).title()
    return d


def parse_guests(text: str) -> List[str]:
    m = re.search(r"Guests?\s*Details\s*(.+?)(?:Arrival|Hotels|Itinerary|$)",
                  text, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    lines = re.findall(r"^\s*\d+[\.\)]\s*(.+?)$", block, re.MULTILINE)
    return [_clean(l) for l in lines if l.strip()]


def parse_flights(text: str) -> Tuple[Optional[dict], Optional[dict]]:
    """TravClan lays arrival + departure side-by-side. OCR reads both
    columns on the same line, so we slice each line into left/right at
    the widest run of spaces and grab date/time/remarks from each half."""
    m = re.search(r"Arrival\s*&\s*Departure\s*Details\s*(.+?)(?:Hotels|Itinerary|Guests|Terms|$)",
                  text, re.IGNORECASE | re.DOTALL)
    if not m:
        return None, None
    block = m.group(1)

    date_time_line = re.search(rf"({DATE})\s*\|\s*({TIME}).*?({DATE})?\s*\|?\s*({TIME})?",
                                block, re.IGNORECASE)
    remarks_line   = re.search(r"Remarks\s*-\s*(.+)$", block, re.IGNORECASE | re.MULTILINE)

    def split_remarks(s: str) -> tuple[str, str]:
        # Slice on 'Remarks -' or the widest gap
        parts = re.split(r"\s*Remarks\s*-\s*", s, flags=re.IGNORECASE)
        if len(parts) >= 2:
            return _clean(parts[0]), _clean(parts[1])
        # Fallback: split on wide gap
        m2 = re.search(r"\s{2,}", s)
        if m2:
            return _clean(s[:m2.start()]), _clean(s[m2.end():])
        return _clean(s), ""

    dt_pairs = re.findall(rf"({DATE})\s*\|\s*({TIME})", block)
    remarks_full = None
    rm = re.search(r"Remarks\s*-\s*(.+)$", block, re.IGNORECASE | re.MULTILINE)
    if rm:
        remarks_full = rm.group(1)
    r_left, r_right = split_remarks(remarks_full) if remarks_full else ("", "")

    def norm_remarks(r: str) -> Optional[str]:
        if not r:
            return None
        r = re.sub(r"\s*\|\|\s*", " · ", r)
        r = re.sub(r"\s+\|\s+", " · ", r)
        r = re.sub(r"\s+", " ", r).strip()
        return r or None

    def build(idx, remarks_str):
        if idx >= len(dt_pairs):
            return None
        d, t = dt_pairs[idx]
        return {"mode": "Flight", "date": _clean(d), "time": _clean(t),
                "remarks": norm_remarks(remarks_str)}

    return build(0, r_left), build(1, r_right)


def parse_hotels(text: str) -> List[dict]:
    """Extract hotels from the voucher OCR text.

    Two OCR layouts are possible:
     1. **Single-column** (Jash, Nilesh, Rajendra): hotel name is adjacent
        (same line or 1-2 lines away) to its 'Check in - date' marker.
        Both blocks live inside a 'Hotels' section between Guests Details
        and Itinerary.
     2. **Two-column** (Virat): the left column has hotel name / location
        / rooms / room-type / meal, and the right column has confirmation
        no / check-in / check-out. OCR reads left column top-to-bottom
        first, then right column. Result: hotel names appear early in the
        OCR text, but check-in dates appear far later (after Itinerary).

    Strategy: find hotel-info blocks (anchored on 'Rooms & Guests') and
    check-in-info blocks (anchored on 'Check in - date') separately, then
    pair them by order.
    """
    # Slice from after Guest Details (or start) to before Terms
    body_m = re.search(r"(?:Guests?\s*Details|Booking\s*Id)(.+?)(?:Terms?\s*&\s*Conditions?|Thank you|$)",
                       text, re.IGNORECASE | re.DOTALL)
    body = body_m.group(1) if body_m else text
    lines = body.split("\n")

    check_in_re  = re.compile(rf"Check\s*in\s*-\s*({DATE}(?:\s*\|\s*{TIME})?)", re.IGNORECASE)
    check_out_re = re.compile(rf"Check\s*out\s*-\s*([^\n]+)", re.IGNORECASE)
    rooms_re     = re.compile(r"Rooms?\s*&\s*Guests?\s*-\s*([^\n]+)", re.IGNORECASE)
    room_type_re = re.compile(r"Room\s*Type\s*-\s*([^\n]+)", re.IGNORECASE)
    meal_re      = re.compile(r"Meal\s*Type\s*-\s*([A-Z]{2,3})", re.IGNORECASE)

    def is_noise(l):
        s = l.strip()
        if not s: return True
        if len(s) < 12 and re.fullmatch(r"[k\*e\u2605\u25c6\s\|tK,;.FOI]+", s, re.IGNORECASE):
            return True
        # Only filter lines that START with 'Confirmation No' as noise (i.e.
        # standalone confirmation lines). Lines where 'Confirmation No'
        # appears at the end after a hotel name have their trail stripped
        # in strip_trails() instead.
        if re.match(r"^\s*Confirmation\s*No", s, re.IGNORECASE): return True
        if re.match(r"^\s*(Rooms?|Room Type|Meal Type|Remarks)\b", s, re.IGNORECASE): return True
        # A line that starts with just OCR-of-stars noise before 'Check in/out'
        # is not a name candidate.
        starts_with_check = re.match(r"^\s*([^\n]{0,25}?)Check\s*(in|out)\s*-", s, re.IGNORECASE)
        if starts_with_check:
            prefix = starts_with_check.group(1).strip()
            if not prefix:
                return True
            if len(prefix) < 20 and re.fullmatch(r"[k\*e\u2605\u25c6\s\|tK,;.FOIaeiouy]+", prefix, re.IGNORECASE):
                return True
        return False

    def strip_trails(s):
        """Remove ' Check in - ...', ' Check out - ...', or ' Confirmation
        No - ...' trailers from a line so what remains is just the hotel
        name."""
        s = re.sub(r"\s*Check\s*(in|out)\s*-.*$", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*Confirmation\s*No\s*-.*$", "", s, flags=re.IGNORECASE)
        return s.strip()

    def looks_like_name(l):
        s = re.sub(r"\s*Confirmation\s*No[^\n]*$", "", l, flags=re.IGNORECASE).strip()
        s = re.sub(r"^[^A-Za-z0-9]+", "", s).strip()
        if len(s) < 4: return False
        return sum(c.isalpha() for c in s) >= 4 and any(c.isupper() for c in s)

    # 1) Hotel-info blocks — anchored on 'Rooms & Guests' (every hotel has one)
    hotel_infos = []
    for idx, ln in enumerate(lines):
        if not rooms_re.search(ln):
            continue
        # Walk back through non-noise lines. The nearest non-noise line
        # before Rooms is the location (city). The one before that is
        # the hotel name (may have a 'Check in - date' or 'Confirmation
        # No - X' trailer OCR'd from a same-line layout — strip those).
        prior_lines = []
        for k in range(idx - 1, max(idx - 10, -1), -1):
            prev = lines[k].strip()
            if not prev or is_noise(prev):
                continue
            cleaned = strip_trails(prev)
            cleaned = re.sub(r"^[^A-Za-z0-9]+", "", cleaned).strip()
            if cleaned:
                prior_lines.append(cleaned)
            if len(prior_lines) >= 3:
                break
        location = None
        name = None
        if len(prior_lines) >= 1:
            # In both single-column and two-column layouts, the closest
            # non-noise line above Rooms is the location (city). The next
            # closest is the hotel name.
            if len(prior_lines) >= 2:
                location = _clean(prior_lines[0])
                name = _clean(prior_lines[1])
            else:
                # Only one candidate — treat as name if it's substantial,
                # otherwise as location
                candidate = prior_lines[0]
                if len(candidate) > 15 or re.search(r"(hotel|resort|villa|inn|palace|suite|spa)", candidate, re.IGNORECASE):
                    name = _clean(candidate)
                else:
                    location = _clean(candidate)
        if not name:
            continue
        h = {"name": name}
        if location: h["location"] = location
        # Extract Rooms/Room Type/Meal from the current and next few lines
        for l in lines[idx:idx+5]:
            mr = rooms_re.search(l)
            if mr and "rooms_guests" not in h:
                h["rooms_guests"] = _clean(mr.group(1)).replace("|", "\u00b7")
            mt = room_type_re.search(l)
            if mt and "room_type" not in h:
                h["room_type"] = _clean(mt.group(1))
            mp = meal_re.search(l)
            if mp and "meal_plan" not in h:
                code = _clean(mp.group(1)).upper()
                h["meal_plan"] = {"CP": "Breakfast", "MAP": "Half Board",
                                  "AP": "Full Board", "EP": "Room Only"}.get(code, code)
        hotel_infos.append(h)

    # 2) Check-in blocks — anchored on 'Check in - date'
    checkin_infos = []
    for idx, ln in enumerate(lines):
        mm = check_in_re.search(ln)
        if not mm:
            continue
        ci = {"check_in": _clean(mm.group(1))}
        # Check-out usually on the next non-empty line
        for l in lines[idx+1:idx+4]:
            mco = check_out_re.search(l)
            if mco:
                ci["check_out"] = _clean(mco.group(1))
                break
        checkin_infos.append(ci)

    # 3) Pair by order — first hotel_info with first checkin_info, etc.
    # If counts don't match, still return whatever we can.
    hotels = []
    for i, h in enumerate(hotel_infos):
        if i < len(checkin_infos):
            h.update(checkin_infos[i])
        hotels.append(h)

    return hotels
def parse_days(text: str) -> List[dict]:
    m = re.search(r"(?:Trip\s+)?Itinerary\s*(.+?)(?:Terms?\s*&\s*Conditions?|Thank you|$)",
                  text, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    block = m.group(1)

    # Weekday is optional: confirmed vouchers read "Day 1: Sat, 08 Aug, 2026"
    # while tentative vouchers read "Day 1: 05 Aug 2026" (no weekday).
    day_pattern = re.compile(
        rf"[\|\s]*Day\s+(\d+):\s*(?:([A-Za-z]{{3}}[a-z]*),?\s+)?({DATE})",
        re.IGNORECASE)
    matches = list(day_pattern.finditer(block))
    days = []
    for i, dm in enumerate(matches):
        n = int(dm.group(1))
        weekday = dm.group(2)
        date_part = _clean(dm.group(3))
        date_display = f"{weekday}, {date_part}" if weekday else date_part
        start = dm.end()
        end = matches[i+1].start() if i + 1 < len(matches) else len(block)
        chunk = block[start:end]

        if re.search(r"\bleisure\b", chunk, re.IGNORECASE):
            days.append({"n": n, "date_display": date_display, "leisure": True, "stops": []})
            continue

        stops = _parse_day_stops(chunk)
        days.append({"n": n, "date_display": date_display, "stops": stops})
    return days


# Tour-type badge labels (BUG2). Edit RHS to change customer-facing wording.
_TOUR_TYPE_LABELS = {
    "PRIVATE":       "Private",
    "SIC":           "SIC (Shared)",
    "SEAT-IN-COACH": "SIC (Shared)",
    "SEAT IN COACH": "SIC (Shared)",
    "TICKETS ONLY":  "Tickets Only",
    "TICKET ONLY":   "Tickets Only",
}

def _tour_type_label(stop_type: str):
    """Map the OCR'd tag to a customer label, or None for transfer/cab stops."""
    key = re.sub(r"\s+", " ", (stop_type or "").upper()).strip()
    key = key.replace("SEAT-IN-COACH", "SEAT IN COACH")
    return _TOUR_TYPE_LABELS.get(key)

# Return / drop time (BUG1). SIC stops read "Return Pick up time - 01:00 PM".
_RETURN_TIME_RE = re.compile(
    r"(?:Return\s*Pick\s*up\s*time|Drop\s*time)\s*[-–:]\s*"
    r"([0-9]{1,2}[:.]?[0-9]{0,2}\s*[AaPp]\.?[Mm]\.?)")

def _find_drop_time(block: str):
    m = _RETURN_TIME_RE.search(block or "")
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).upper().replace(".", "").strip()


def _parse_day_stops(chunk: str) -> List[dict]:
    """Every stop in TravClan itineraries has a title line ending with
    ' | PRIVATE', ' | SIC', ' | TICKETS ONLY', etc. Anchor on those.

    A stop block runs from its title line to the next title line (or the
    end of the day chunk). Inside a block we look for optional Pickup +
    Drop, optional Remarks, and any descriptive prose."""
    STOP_TYPE = r"PRIVATE|SIC|TICKETS\s*ONLY|SEAT[\s\-]IN[\s\-]COACH|\d+\s*Cab(?:\(s\))?"
    title_re = re.compile(rf"^([^\n]+?)\s*\|\s*({STOP_TYPE})[^\n]*$",
                          re.MULTILINE | re.IGNORECASE)
    matches = list(title_re.finditer(chunk))
    if not matches:
        return []

    stops = []
    for i, m in enumerate(matches):
        title = _clean(m.group(1))
        stop_type = m.group(2).upper()
        # If title starts with junk (e.g. leading "| " from OCR of divider),
        # strip it
        title = re.sub(r"^[^\w]+", "", title).strip()
        if not title:
            continue

        # Everything from end-of-title to next title (or end of chunk)
        body_start = m.end()
        body_end = matches[i+1].start() if i+1 < len(matches) else len(chunk)
        body = chunk[body_start:body_end]

        # Pickup + Drop on one line looks like:
        #   "Pickup from: X ay Drop at: Y"
        # OCR of the car icon between them varies (ay, fay, =, >, ~). Split
        # on 'Drop at' to isolate pickup value; strip trailing icon glyphs.
        pickup = None
        drop = None
        pickup_time = None
        pd_line = re.search(r"Pickup\s*from[:\s]+([^\n]+)", body, re.IGNORECASE)
        if pd_line:
            raw = pd_line.group(1)
            drop_split = re.split(r"\s+\S{0,4}\s*Drop\s*at[:\s]+", raw, maxsplit=1, flags=re.IGNORECASE)
            if len(drop_split) == 2:
                pickup = re.sub(r"[^\w\)]+$", "", drop_split[0]).strip()
                drop   = _clean(drop_split[1])
            else:
                pickup = re.sub(r"[^\w\)]+$", "", raw).strip()
        # Sometimes Drop is on its own line
        if not drop:
            drop_line = re.search(r"Drop\s*at[:\s]+([^\n]+)", body, re.IGNORECASE)
            if drop_line:
                drop = _clean(drop_line.group(1))

        # Pickup time
        pt = re.search(r"Pickup\s*time\s*-\s*([^\n]+)", body, re.IGNORECASE)
        if pt:
            pickup_time = _clean(pt.group(1))
            # On SIC stops the pickup + return times share one OCR line
            # ("... 02:00 PM Return Pick up time - 08:30 PM"). Keep only the
            # pickup time; the return time is captured separately as drop_time.
            pickup_time = re.split(r"\s*Return\s*Pick", pickup_time, flags=re.IGNORECASE)[0].strip()

        # Remarks (single line following)
        rmk = re.search(r"Remarks:\s*([^\n]+)", body, re.IGNORECASE)
        remarks = _clean(rmk.group(1)) if rmk else None

        # Description = first prose paragraph after title/pickup/remarks that
        # isn't itself Remarks/Pickup/Drop (even if it starts with an OCR
        # icon glyph like em-dash from the car symbol).
        desc = None
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        for para in paras:
            if re.search(r"\b(Pickup\s*from|Drop\s*at|Pickup\s*time)\b", para, re.IGNORECASE):
                continue
            if re.match(r"^\s*Remarks\b", para, re.IGNORECASE):
                continue
            if len(para) < 40:
                continue
            desc = _clean(para)
            break

        # Tour-type badge (BUG2): PRIVATE / SIC / TICKETS ONLY / SEAT-IN-COACH
        # are shown to the customer. Cab-count descriptors (transfers) are not.
        tour_type = _tour_type_label(stop_type)

        # Drop / return-pickup time (BUG1): SIC activity stops carry a
        # "Return Pick up time - HH:MM PM". Private/transfer stops have none.
        drop_time = _find_drop_time(body)

        stop = {"title": title}
        if tour_type:   stop["tour_type"] = tour_type
        if pickup:      stop["pickup"] = pickup
        if pickup_time: stop["pickup_time"] = pickup_time
        if drop:        stop["drop"] = drop
        if drop_time:   stop["drop_time"] = drop_time
        if remarks:     stop["remarks"] = remarks
        if desc:        stop["description"] = desc
        stops.append(stop)
    return stops

# Leading OCR'd bullet glyph (BUG3). Tesseract renders "•" as any of these.
# Only strips when followed by whitespace + real content (capital/digit/quote/
# paren/URL-ish), so a legitimate lowercase-leading term is never touched.
_LEAD_BULLET = re.compile(r"^\s*[•·▪◦‣∙*+_,.\-–—©@¢°oe]\s+(?=[A-Z0-9\"'(h])")

def _strip_lead_bullet(s: str) -> str:
    return _LEAD_BULLET.sub("", s or "").strip()


def parse_terms(text: str) -> List[str]:
    """Terms can be formatted as bullet lists (Jash, Nilesh) or as
    blank-line-separated paragraphs (Sachin, Rajendra). Try both
    strategies and pick the one that gives a clean, non-fragmented
    result."""
    m = re.search(r"Terms?\s*&\s*Conditions?\s*(.+?)(?:Thank you|$)",
                  text, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    block = m.group(1)

    # Strategy A: bullet-based. Split on a newline followed by any OCR'd
    # bullet glyph (e * _ + , . • · ◆ - – © @ o) or a numbered-list marker.
    # Normalise the very first bullet too — it has no preceding newline, so
    # inject one so the split treats it like the rest.
    block_a = "\n" + block.lstrip()
    items_a = re.split(r"\n\s*(?:[eo•·▪◦‣∙*+_,.©@◆\-–—]|\d+\.)\s+", block_a)
    parsed_a = []
    for item in items_a:
        it = _clean(item)
        for sub in re.split(r"\s{2,}(?=[A-Z][a-z])", it):
            sub = _clean(sub)
            if 15 < len(sub) < 700 and not sub.lower().startswith(("thank", "have a safe")):
                parsed_a.append(sub)

    # Strategy B: blank-line paragraphs, then merge fragments that OCR
    # soft-broke apart. Rule: if the previous item ends with sentence-
    # ending punctuation (. ! ?), start a new term; otherwise the current
    # line is a continuation and gets merged into the previous.
    raw_paras = [_clean(p) for p in re.split(r"\n\s*\n", block) if _clean(p)]
    merged = []
    for p in raw_paras:
        if merged:
            prev = merged[-1]
            if prev.rstrip()[-1:] not in ".!?":
                merged[-1] = f"{prev} {p}"
                continue
        merged.append(p)

    # Sanitize any residual vendor URLs (e.g. TravClan CDN links to
    # third-party forms) — replace with a neutral note. Keep any Alike/
    # official URLs untouched.
    def _sanitize(s: str) -> str:
        # Strip any URL that contains 'travclan' (case-insensitive)
        return re.sub(r"https?://\S*travclan\S*", "(link available on request)",
                      s, flags=re.IGNORECASE)

    parsed_b = [_sanitize(x) for x in merged
                if 15 < len(x) < 1200 and not x.lower().startswith(("thank", "have a safe"))]

    # Also sanitize strategy A output
    parsed_a = [_sanitize(x) for x in parsed_a]

    # Choose whichever produces a reasonable, non-fragmented result. If
    # both look sensible, prefer the one that produces MORE items (more
    # terms extracted is generally better than fewer) but only if the
    # winner is under a reasonable ceiling that suggests genuine content
    # rather than over-fragmentation.
    def looks_fragmented(items):
        if not items:
            return False
        # Fragments: items that don't end with sentence-ending punctuation
        no_end = sum(1 for x in items if x.rstrip()[-1:] not in ".!?:")
        return no_end / len(items) > 0.35

    a_ok = parsed_a and not looks_fragmented(parsed_a)
    b_ok = parsed_b and not looks_fragmented(parsed_b)

    if a_ok and b_ok:
        chosen = parsed_a if len(parsed_a) >= len(parsed_b) else parsed_b
    elif a_ok:
        chosen = parsed_a
    elif b_ok:
        chosen = parsed_b
    else:
        # Both look fragmented — fall back to whichever has more content
        chosen = parsed_a if len(parsed_a) >= len(parsed_b) else parsed_b

    # Final safety net (BUG3): strip any residual leading bullet glyph that
    # survived either strategy (esp. Strategy B, which doesn't split on them).
    return [_strip_lead_bullet(x) for x in chosen]


def _match_thumbs_to_stops(pdf_path: str, thumb_dir: str,
                            pages_text: List[str], days: List[dict]) -> None:
    """Assign extracted thumbnails to stops in-place by y-position on the
    source page. TravClan PDFs have no text layer, so we use Tesseract's
    per-word bounding boxes (via `image_to_data`) to find each stop
    title's y-position, then attach each thumb to the closest stop title
    above it on the same page."""
    thumbs = extract_embedded_images(pdf_path, thumb_dir)
    if not thumbs:
        return

    doc = fitz.open(pdf_path)

    # 1) For each thumb, get its PDF-space y position (points). Extract
    #    image xref → rect pairs by re-inspecting the source pages.
    thumb_positions = []  # [{page, y_pdf, path}]
    seen_paths = set()
    for t in thumbs:
        if t["path"] in seen_paths:
            continue
        page = doc[t["page"] - 1]
        # get_images returns tuples; find the xref corresponding to this
        # extraction index and get its placement rect
        images = page.get_images(full=True)
        if t["index"] < len(images):
            xref = images[t["index"]][0]
            try:
                rects = page.get_image_rects(xref)
                if rects:
                    r = rects[0]
                    thumb_positions.append({"page": t["page"], "y_pdf": r.y0, "path": t["path"]})
                    seen_paths.add(t["path"])
            except Exception:
                pass

    # 2) Per page, use Tesseract with per-word bounding boxes to find
    #    stop title positions. A stop title is a line ending with
    #    "| PRIVATE" or "| SIC" (or variants).
    STOP_TOKENS = {"PRIVATE", "SIC", "TICKETS", "CAB", "SEAT"}
    page_titles = {}  # page_num → [(y_pdf, stop_ref)]

    for page_num in range(1, len(doc) + 1):
        page = doc[page_num - 1]
        page_titles[page_num] = []
        # Rasterize the page at 200 DPI (cheaper than 300 for positions)
        pix = page.get_pixmap(dpi=200)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        # Group words into lines by (block_num, par_num, line_num)
        lines = {}
        for i, txt in enumerate(data["text"]):
            if not txt.strip():
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append({
                "text": txt, "top": data["top"][i], "left": data["left"][i]
            })
        # DPI conversion for y: image_y / 200 * 72 = pdf_y (points)
        # (200 DPI = image pixels per inch; 72 pt = 1 inch)
        px_to_pt = 72.0 / 200.0
        # Convert each line to a joined string + top position
        for line_words in lines.values():
            words = [w["text"] for w in line_words]
            joined = " ".join(words)
            # Does this line contain a stop-type marker?
            if not any(tok in joined.upper() for tok in STOP_TOKENS):
                continue
            if "|" not in joined:  # must have the | before type
                continue
            y_pdf = min(w["top"] for w in line_words) * px_to_pt
            page_titles[page_num].append((y_pdf, joined))
        page_titles[page_num].sort(key=lambda x: x[0])

    doc.close()

    # 3) Build a flat list of all stops in document order
    all_stops = []
    for day in days:
        for stop in day.get("stops", []):
            all_stops.append(stop)

    # 4) For each page, match its page_titles to stops in all_stops order.
    #    We do a greedy match: walk through all_stops sequentially, pairing
    #    each with the next page_titles entry (across all pages, in order).
    all_page_titles = []  # (page, y_pdf, title_text) in document order
    for pnum in sorted(page_titles):
        for y, txt in page_titles[pnum]:
            all_page_titles.append((pnum, y, txt))

    # Zip: assume the k-th page_title corresponds to the k-th stop. This
    # holds because both lists are in document order.
    stop_positions = []  # [(page, y_pdf, stop_ref)]
    for stop, (pnum, y, _) in zip(all_stops, all_page_titles):
        stop_positions.append((pnum, y, stop))

    # 5) For each thumb, find the stop on the same page with the closest
    #    y_pdf above the thumb.
    for tr in thumb_positions:
        candidates = [(y, s) for (p, y, s) in stop_positions
                      if p == tr["page"] and y <= tr["y_pdf"]]
        if not candidates:
            # No stop above on this page — try last stop on prior page
            prior = [(p, y, s) for (p, y, s) in stop_positions if p < tr["page"]]
            if prior:
                _, _, best = prior[-1]
                best.setdefault("thumbs", []).append(tr["path"])
            continue
        candidates.sort(key=lambda x: x[0])
        best = candidates[-1][1]  # closest below
        best.setdefault("thumbs", []).append(tr["path"])


def extract(pdf_path: str, thumb_dir: Optional[str] = None) -> dict:
    """OCR + parse a TravClan voucher into the renderer's data shape.
    If thumb_dir is provided, embedded thumbnails are also extracted and
    attached to stops by position."""
    pages = ocr_pdf(pdf_path)
    text = "\n".join(pages)

    meta   = parse_booking_meta(text)
    arrival, departure = parse_flights(text)
    hotels = parse_hotels(text)
    days   = parse_days(text)
    terms  = parse_terms(text)
    guests = parse_guests(text)

    if thumb_dir:
        try:
            _match_thumbs_to_stops(pdf_path, thumb_dir, pages, days)
        except Exception:
            pass  # thumbnails are best-effort; don't fail the whole extract

    dest = meta.get("destination", "") or ""
    guest_lead = meta.get("guest_lead", "")
    guest_lead_first = guest_lead.split()[0] if guest_lead else ""

    return {
        "trip": {
            "booking_id":          meta.get("vendor_booking_id", ""),
            "destination":         dest,
            "guest_lead":          guest_lead,
            "guest_lead_first":    guest_lead_first,
            "travel_date_display": meta.get("travel_date", ""),
            "nights":              int(meta.get("nights", 0)) if meta.get("nights") else 0,
            "pax_display":         meta.get("pax", ""),
            "guests":              guests,
            "arrival":             arrival,
            "departure":           departure,
            "hotels":              hotels,
            "days":                days,
            "terms":               terms,
        },
        "contacts": {
            "advisor":         "",
            "on_ground_name":  "",
            "on_ground_phone": "",
            "emergency":       "+91 95133 92429",
            "careline":        "+91 88000 25030",
        },
        "_ocr_pages_raw": pages,
        "_ocr_warnings":  _sanity_check(meta, hotels, days, guests, arrival, departure),
        "_vendor_booking_id": meta.get("vendor_booking_id", ""),
    }


def _sanity_check(meta, hotels, days, guests, arrival, departure) -> List[str]:
    w = []
    if not meta.get("vendor_booking_id"):
        w.append("Booking ID not detected — verify and replace with Infinity Order ID.")
    if not meta.get("destination"):
        w.append("Destination not detected — set manually.")
    if not meta.get("travel_date"):
        w.append("Travel date not detected — set manually.")
    if not hotels:
        w.append("No hotels detected — verify (some vouchers legitimately have zero hotels).")
    if not days:
        w.append("No day-by-day itinerary detected — this is unusual.")

    # Guest list vs Pax count sanity
    import re as _re
    if guests and meta.get("pax"):
        m = _re.search(r"(\d+)\s*Adult", meta["pax"])
        if m:
            adult_pax = int(m.group(1))
            if len(guests) < adult_pax:
                w.append(f"Only {len(guests)} guest name(s) detected but Pax says {adult_pax} Adults — "
                         f"the source voucher may have omitted the co-traveller names. Add them in the "
                         f"'Guests' field below.")

    if arrival and not departure:
        w.append("Arrival detected but Departure missing — source may have an incomplete "
                 "flight block; add departure details in the Arrival & Departure section.")
    return w


if __name__ == "__main__":
    import sys, json
    result = extract(sys.argv[1])
    result.pop("_ocr_pages_raw", None)
    print(json.dumps(result, indent=2, default=str))
