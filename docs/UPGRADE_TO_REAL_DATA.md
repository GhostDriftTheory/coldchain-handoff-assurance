# Connecting Real Data — Step-by-Step Guide

This guide describes how to move from the synthetic-data PoC to a real-data PoC,
and what must be true before each step.

---

## Step 1: Check the graduation checklist

Before connecting any real or anonymised data, confirm all of the following.

### Input validity
- [ ] All required fields are populated for every handoff leg
- [ ] `temp_missing` / `temp_records_complete` are set correctly for every leg
- [ ] `max_temp_sample_gap_minutes` can be derived from actual logger records
- [ ] Temperature timestamps are strictly increasing (logger parser must guarantee this)
- [ ] No NaN / ±infinity values appear in any numeric field

### Evidence consistency
- [ ] `evidence_ok` and its sub-flags (`seal_intact`, `custody_complete`,
      `logger_calibrated`, `temp_records_complete`) are set by agreed operational rules,
      not defaults
- [ ] There is a documented definition of "custody complete" agreed with logistics partners

### Chain stop policy
- [ ] The team has decided whether `--stop-on-fail` (default) or `--continue-after-fail`
      is used operationally, and why
- [ ] There is a documented response procedure for each verdict (HOLD / QA_REVIEW / REJECT)

### Test suite
- [ ] `pytest test_poc_handoff_pde.py` passes with 0 failures
- [ ] At least one real or anonymised data leg has been run through each of the
      four verdict types (RELEASE / HOLD / QA_REVIEW / REJECT)

---

## Step 2: JSON field specification

Every handoff leg in the `handoffs` array must conform to this schema.

### Required fields

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `shipment_id` | string | non-empty, no leading/trailing whitespace | Identifies the shipment |
| `measure_id` | string | must equal `"lifescience.coldchain.handoff.2to8c.v1"` | Profile binding |
| `duration_minutes` | number | finite, ≥ 0 | Length of this handoff leg |
| `temperatures` | array of `[number, number]` | each time ≥ 0 and finite; each temp finite; timestamps strictly increasing | `[[minutes_elapsed, temp_celsius], …]` |
| `temp_missing` | bool (`true`/`false`) | JSON bool only; strings/ints rejected | `true` if logger data unavailable |
| `evidence_ok` | bool | JSON bool only | Overall evidence summary flag |
| `custody_complete` | bool | JSON bool only | Custody handoff records complete |
| `seal_intact` | bool | JSON bool only | Physical tamper seal confirmed intact |
| `logger_calibrated` | bool | JSON bool only | Logger calibration records available |
| `temp_records_complete` | bool | JSON bool only | Must be `false` when `temp_missing=true` |

**When `temp_missing=true`:** `temperatures` must be `[]` and `temp_records_complete` must be `false`.

### Optional fields

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `max_temp_sample_gap_minutes` | number | `0` | Largest gap between consecutive samples; finite, ≥ 0 |
| `handoff_from` | string or null | `null` | Origin node label (shown in output; not evaluated) |
| `handoff_to` | string or null | `null` | Destination node label (shown in output; not evaluated) |
| `pallet_id` | string or null | `null` | Pallet identifier (stored in decision record; not evaluated) |
| `logger_id` | string or null | `null` | Logger serial number (stored; not evaluated) |

**String fields:** Empty string (`""`) is rejected for `handoff_from` / `handoff_to`.
If the value is unknown, omit the key or set it to `null`.

### Future extension fields (not evaluated in current PoC)

| Field | Planned use |
|-------|-------------|
| `receiver_auth_present` | Receiver authentication confirmation. Reserved for a future module. Pass it or omit it — the PoC ignores it. |

---

## Step 3: Sample JSON files

### Valid input — all records present

```json
{
  "scenario_name": "Normal transit — Shipper to Hospital",
  "handoffs": [
    {
      "shipment_id": "SHIP-2026-001",
      "measure_id": "lifescience.coldchain.handoff.2to8c.v1",
      "duration_minutes": 22.0,
      "temperatures": [
        [0, 4.8], [5, 5.1], [10, 5.0], [15, 4.9], [20, 5.2], [22, 5.0]
      ],
      "temp_missing": false,
      "max_temp_sample_gap_minutes": 5.0,
      "handoff_from": "Shipper-A",
      "handoff_to": "Carrier-X",
      "evidence_ok": true,
      "custody_complete": true,
      "seal_intact": true,
      "logger_calibrated": true,
      "temp_records_complete": true
    }
  ]
}
```

### Evidence gap — logger disconnected mid-transit

```json
{
  "scenario_name": "Evidence gap — logger disconnected",
  "handoffs": [
    {
      "shipment_id": "SHIP-2026-002",
      "measure_id": "lifescience.coldchain.handoff.2to8c.v1",
      "duration_minutes": 24.0,
      "temperatures": [],
      "temp_missing": true,
      "max_temp_sample_gap_minutes": 0.0,
      "handoff_from": "Carrier-X",
      "handoff_to": "Hub-B",
      "evidence_ok": true,
      "custody_complete": true,
      "seal_intact": true,
      "logger_calibrated": true,
      "temp_records_complete": false
    }
  ]
}
```
**Expected verdict:** 🟡 HOLD (temp_records_complete=false fails evidence gate)

### Type-error example — string bool (will be rejected)

```json
{
  "scenario_name": "Type error — string instead of bool",
  "handoffs": [
    {
      "shipment_id": "SHIP-ERR-001",
      "measure_id": "lifescience.coldchain.handoff.2to8c.v1",
      "duration_minutes": 20.0,
      "temperatures": [[0, 5.0], [10, 5.1]],
      "temp_missing": false,
      "max_temp_sample_gap_minutes": 10.0,
      "evidence_ok": true,
      "custody_complete": true,
      "seal_intact": "false",
      "logger_calibrated": true,
      "temp_records_complete": true
    }
  ]
}
```
**Expected outcome:** `INPUT ERROR: seal_intact must be JSON true or false …`  
(exit 1, no verdict issued)

---

## Step 4: Adjust thresholds per-product and per-lane

The current profile uses conservative PoC starter values.
Once your product and lane are confirmed, adjust `_build_profile()` in `poc_handoff_pde.py`:

```python
def _build_profile() -> ProductProfile:
    return ProductProfile(
        profile_id="lifescience_2to8c_standard_handoff",
        profile_version="v1",
        temperature_lower_c=2.0,
        temperature_upper_c=8.0,
        alpha=ThresholdBand(
            d_plus=0.3,           # ← adjust based on product stability data
            d_minus=0.3,
            a_plus=3.0,           # ← adjust based on MKT calculation
            a_minus=3.0,
            max_gap_minutes=10.0, # ← adjust based on lane SLA
            duration_minutes=30.0,
        ),
        beta=ThresholdBand(
            d_plus=2.0,
            d_minus=2.0,
            a_plus=30.0,
            a_minus=30.0,
            max_gap_minutes=45.0,
            duration_minutes=90.0,
        ),
        requirements=RequirementFlags(),
        notes="Your product / lane — validated [date].",
    )
```

After any threshold change, re-run the full test suite to confirm boundary behaviour.

---

## Step 5: Connect real data sources

Replace the JSON file loading in `_load()` with your data source:

```python
# Current: load from JSON file
name, handoffs = _load("my_scenario.json")

# Option A: fetch from database
handoffs = db.fetch_handoffs(shipment_id="...", date="...")

# Option B: parse logger CSV export
handoffs = parse_logger_export("logger_2026_04.csv")
```

The evaluation engine (`evaluate_handoff()` / `run_chain()`) does not change.
Only the data-loading layer is replaced.

**Required by your parser:**
- Convert logger timestamps to minutes elapsed since leg start
- Guarantee strictly increasing timestamps (sort and deduplicate before passing)
- Derive `max_temp_sample_gap_minutes` from the actual sample sequence
- Set `temp_missing=true` / `temp_records_complete=false` when logger data is absent

---

## What to add when graduating to production

| Capability | Notes |
|------------|-------|
| Hardware-signed logger data | Verify sensor provenance before evaluation |
| Audit trail | Store `canonical_payload()` of each `DispositionDecision` |
| Master data | Node codes for `handoff_from` / `handoff_to`, normalised against a master list |
| Alerting | Trigger on HOLD / QA_REVIEW / REJECT verdicts automatically |
| Attribution | Responsibility assignment across companies is a separate module |

---

> PoC starter profile — NOT a regulatory claim.
