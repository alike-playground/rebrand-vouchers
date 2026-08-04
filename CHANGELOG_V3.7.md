# V3.7 — UAT patch (Singapore vouchers: takcko, tayk1v)

All changes are in `extractor.py` and `templates/voucher.html`. No new
dependencies. Backward-compatible with existing data (jash_data.json renders
unchanged).

## Fixed
1. **Terms leading with "e" (BUG3).** Tesseract renders the source "•" bullet
   inconsistently as e / * / _ / + / , . `parse_terms` now strips the leading
   OCR'd bullet glyph on every returned term (covers both bullet and paragraph
   strategies) and normalises the first bullet, which had no preceding newline.

2. **SIC / Private / Tickets Only badge (BUG2).** The tour-type tag was detected
   for stop segmentation but discarded. Now stored as `stop["tour_type"]`
   ("Private", "SIC (Shared)", "Tickets Only") and rendered as a badge beside the
   stop title. Transfer stops (Cab-count descriptors) are intentionally un-badged.

3. **Drop / return-pickup time (BUG1).** SIC activity stops carry a
   "Return Pick up time - HH:MM PM". `_parse_day_stops` now captures it into
   `stop["drop_time"]` (the template already rendered this field). Private and
   transfer stops correctly have none.

## Also fixed during UAT (pre-existing, surfaced by the two test vouchers)
4. **Empty itinerary on tentative vouchers.** `parse_days` required a weekday
   ("Day 1: Sat, 08 Aug, 2026"). Tentative vouchers use "Day 1: 05 Aug 2026"
   with no weekday and parsed zero days. Weekday is now optional; the confirmed
   (weekday) format is unaffected.

5. **Pickup time swallowing the return time.** On SIC stops both times share one
   OCR line; `pickup_time` greedily captured the trailing "Return Pick up time -
   ...". Now truncated before "Return"; the return time still lands in drop_time.

## Known limitations (unchanged, ops verify-and-edit handles these)
- Mid-line OCR garble in the source's multi-column "Instructions for Guests"
  table (e.g. "as 0 90", "wait up to utes") is a source OCR-quality issue.
- OCR occasionally drops a bare leisure-day header (tayk1v Day 2); add in review.
