# Workflow: first-run setup

Objective: get Procurement / Supply AI ("The Quartermaster") from a fresh
clone to a working demo, then to real config, in one sitting.

## Steps

1. **Install and check.**
   ```bash
   make setup
   make doctor
   ```
   `make setup` creates the virtualenv, installs `requirements.txt`, and
   copies `.env.example` -> `.env` and every `config/*.example.yaml` ->
   `config/*.yaml` (only if those files do not exist yet - it never
   overwrites your own copies). `make doctor` will show a `FAIL` on "hotel
   identity" right after setup - expected, it means the property name is
   still the shipped placeholder ("Hotel Aurora"). Everything else should
   be `ok` or `warn`.

2. **Run the demo.** No credentials needed.
   ```bash
   make demo
   ```
   Expect to see four supplier orders forecast from the sample property's
   occupancy and restaurant covers, one flagged for a price check, and the
   line `DEMO OK — 4 items processed, 4 drafted, 0 sent (shadow)`. If you do
   not see that, stop and read `workflows/99-troubleshooting.md`.

3. **Fill in the property.** Edit `config/hotel.yaml` (name, address,
   contact, room count, languages). Then:
   ```bash
   cp knowledge/suppliers.example.md       knowledge/suppliers.md
   cp knowledge/ordering-policy.example.md knowledge/ordering-policy.md
   cp knowledge/disclosure.example.md      knowledge/disclosure.md
   ```
   Replace the sample supplier list with the property's real suppliers -
   name, what they supply, lead time, and whether they take orders online.
   See `knowledge/README.md`. `disclosure.md` is the one-sentence
   AI-disclosure line appended to every WhatsApp order message
   (`Messaging.with_disclosure()`, `channel: whatsapp` in
   `docs/integrations.md`), also the EU AI Act Article 50 line.

4. **Replace the sample catalogue.** `fixtures/hotel/supply_items.json` is
   an invented 16-SKU list built around two spec anchors (sea bass, bath
   towels). A real property needs its own item list: id, name, category
   (`linen` / `fnb` / `other`), unit, par level, current on-hand, daily use
   per occupied room, supplier, whether that supplier has a portal, unit
   cost and its 90-day baseline, and lead time in days. Ask your Claude Code
   session to help build this from your actual order history - it is the
   single most important file in this repo to get right.

5. **Pick how the agent thinks.** `config/hotel.yaml`'s `llm.provider`
   starts as `interactive` - it asks you, in this Claude Code session,
   instead of calling a model. That costs nothing extra and is the best way
   to see how the Quartermaster reasons. `docs/how-it-works.md` and
   `docs/safety.md` cover the other three providers (`mock`, `claude-code`,
   `anthropic`).

6. **Connect a real occupancy feed (optional for now).**
   `systems.pms.adapter` in `config/hotel.yaml` starts as `mock`, which only
   ever sees the sample fixtures. `docs/integrations.md` covers `csv` (works
   with any PMS export) and `cloudbeds`. Restaurant covers have no adapter -
   see `docs/integrations.md` "Restaurant covers" for how to feed real ones
   in.

7. **Re-check.**
   ```bash
   make doctor
   ```
   Once the property name is real and the catalogue is your own, the "hotel
   identity" line turns green. Move on to `workflows/10-procurement.md` to
   run the loop for real.
