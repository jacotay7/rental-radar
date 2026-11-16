---
name: tune-search
description: Adjust a Rental Radar search from the user's feedback ("too many apartments", "why is this ranked so high?", "I'd never live there", "too many emails"). Finds the responsible score component and changes the smallest config knob, then shows before and after. Use when someone reacts to their results or runs /tune-search.
---

# Tune a search from feedback

Find the search: `searches/*/config.yaml`, `$RADAR_CONFIG`, or ask. Use `radar --config <path> ...` below.

1. **Get concrete examples.** If they mention a listing, find it (`radar top -n 40`, or search the
   output for the address or price) and run `radar show <uid>`. It prints the pros and cons, the score
   components (each 0–1), the weighted base and every adjustment.
2. **Find the cause.** Which component or adjustment explains the gap between the score and what they
   expected? Check docs/scoring.md for the formulas.
3. **Change the smallest knob.** Use the "Tuning recipes" table in docs/scoring.md. Prefer, in order:
   - a neighborhood score or pros/cons in `areas.yaml`
   - `prefer_areas` / `avoid_areas`
   - one weight
   - a threshold
   - `home_types`, `walkup_penalty`, `below_ideal_penalty`

   Change code (`analyze.py`, with a test) only when a signal is genuinely missing, e.g. "it doesn't
   notice there's no parking".
4. **Show the effect.** Note the score and rank of 3–5 relevant listings, edit, run `radar rescore`, and
   show before and after in a small table. Check that the change didn't push unrelated good listings
   down.
5. **Confirm** with them, and explain what changed in one or two sentences.

Too many or too few messages: count how many listings sit above each threshold
(`radar top -n 100`). Move `email_threshold` (daily digest) or `top_pick_threshold` (instant alerts)
until the daily volume feels right. 5–15 new digest listings a day is usually comfortable.

Suspected scam: follow AGENTS.md "Investigate a suspected scam". Never weaken a scam check to let one
listing through without telling them about the trade-off.
