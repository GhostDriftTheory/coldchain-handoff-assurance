# Cold-Chain Handoff Quality Judgement — PoC

> **This is a proof-of-concept using synthetic data.**  
> It demonstrates the judgement logic only. Not a regulatory claim.  
> Validate thresholds per-product and per-lane before any production use.

---

## What this PoC shows

Each time a medical product changes hands in a cold chain (shipper → carrier → hub → hospital),
this PoC evaluates that handoff and produces one of four verdicts:

| Verdict | Meaning | Action |
|---------|---------|--------|
| ✅ **RELEASE** | Evidence complete; all metrics within profile | Proceed to next leg |
| 🟡 **HOLD** | Evidence gap (missing records / broken seal) | Isolate; investigate |
| 🟠 **QA_REVIEW** | Temperature excursion in the gray zone (alpha < metric ≤ beta) | QA review before proceeding |
| 🔴 **REJECT** | Fatal breach — metric exceeds beta threshold | Discard; do not distribute |

When a non-RELEASE verdict occurs, **the chain stops by default** — downstream legs are not
evaluated because their integrity cannot be assumed. The investigation anchor
(last good leg / first failed leg) is displayed immediately.

Three things are shown:
1. **止める** (Stop) — halt the chain at the first issue
2. **残す** (Preserve) — record the full judgement with a fixed ruleset digest
3. **辿る** (Trace) — identify exactly where the chain broke

---

## Quick start

Python 3.8+. No external libraries required.

```bash
# Built-in 3-scenario demo (no JSON file needed)
python poc_handoff_pde.py

# Single-file evaluation
python poc_handoff_pde.py sample_beta_breach_reject.json

# Multi-leg chain — stops at first non-RELEASE (default)
python poc_handoff_pde.py --chain sample_chain_stop_demo.json

# Multi-leg chain — evaluate all legs
python poc_handoff_pde.py --chain sample_chain_stop_demo.json --continue-after-fail

# Write Markdown summary report
python poc_handoff_pde.py --chain sample_chain_stop_demo.json --write-summary

# Invalid input examples (INPUT ERROR displayed, exit 1)
python poc_handoff_pde.py sample_invalid_empty_temps.json
python poc_handoff_pde.py sample_invalid_negatives.json

# Run tests (pytest required)
pytest test_poc_handoff_pde.py -v
```

---

## Files

```
poc_handoff_pde.py              Main implementation (single file, no dependencies)
README.md                       This file
UPGRADE_TO_REAL_DATA.md         Step-by-step guide to connecting real data
SECURITY_DESIGN.md              Input protection and known boundaries
RED_TEAM_REPORT.md              Red-team-style scenario testing report (30 scenarios)

sample_beta_breach_reject.json  Verdict demo: REJECT (d_plus > beta)
sample_chain_stop_demo.json     Verdict demo: HOLD → chain stop (3 legs)
sample_invalid_empty_temps.json Invalid input: temperatures=[] with temp_missing=false
sample_invalid_negatives.json   Invalid input: duration_minutes < 0
sample_invalid_timestamps.json  Invalid input: unsorted timestamps
sample_invalid_evidence.json    Edge case: evidence_ok=false, all sub-flags true → WARN + HOLD

test_poc_handoff_pde.py         Test suite (pytest)
summary.md                      Example summary report output
```

**Two placement layouts are supported:**

```
# Flat layout (ZIP default — test file alongside main script)
project/
  poc_handoff_pde.py
  test_poc_handoff_pde.py    ← run: pytest test_poc_handoff_pde.py -v
  sample_*.json ...

# Subdirectory layout
project/
  poc_handoff_pde.py
  tests/
    test_poc_handoff_pde.py  ← run: pytest tests/test_poc_handoff_pde.py -v
```

---

## Judgement logic

```
Input received
  │
  ├─ validate_handoff_input() ──── malformed? ──→ INPUT ERROR (exit 1, no verdict)
  │
  ↓ valid input
  ├─ 1. Evidence gate ─── missing records / broken seal? ──→ HOLD
  ├─ 2. Fatal gate ────── any metric > beta?              ──→ REJECT
  ├─ 3. Auto-release ──── all metrics ≤ alpha?            ──→ RELEASE
  └─ 4. Gray zone ─────── alpha < any metric ≤ beta       ──→ QA_REVIEW
```

Decision order is fixed and auditable. The ruleset digest binds each verdict
to the exact profile version used.

**Important distinction:**
- `HOLD` / `REJECT` / `QA_REVIEW` — valid input, quality verdict
- `INPUT ERROR` — malformed input, not a quality verdict; fix the data upstream

---

## Profile thresholds (PoC starter — 2–8°C)

| Metric | Alpha (auto-release ≤) | Beta (fatal >) |
|--------|----------------------|----------------|
| d_plus [°C] | 0.3 | 2.0 |
| d_minus [°C] | 0.3 | 2.0 |
| a_plus [°C·min] | 3.0 | 30.0 |
| a_minus [°C·min] | 3.0 | 30.0 |
| gap [min] | 10 | 45 |
| duration [min] | 30 | 90 |

**Comparison rule (single source of truth in `_exceeds()`):**
- `observed > threshold` → breach / not-auto-release
- `observed == threshold` → borderline stays in zone (not a breach)

**Note on floating-point precision:** Beta thresholds (2.0, 30.0, 90.0) are exactly
representable in IEEE-754 and comparisons are stable. The alpha d_plus threshold (0.3°C)
is not exactly representable. Measurements very close to 0.3°C may land on either side
depending on sensor rounding. Production deployments should apply appropriate measurement
margins. See `SECURITY_DESIGN.md` §Known Boundaries.

---

## Input format

```json
{
  "scenario_name": "optional label",
  "handoffs": [
    {
      "shipment_id":                "SHIP-001",
      "measure_id":                 "lifescience.coldchain.handoff.2to8c.v1",
      "duration_minutes":           20.0,
      "temperatures":               [[0, 5.0], [10, 5.3], [20, 5.1]],
      "temp_missing":               false,
      "max_temp_sample_gap_minutes": 10.0,
      "handoff_from":               "Shipper-A",
      "handoff_to":                 "Carrier-X",
      "evidence_ok":                true,
      "custody_complete":           true,
      "seal_intact":                true,
      "logger_calibrated":          true,
      "temp_records_complete":      true
    }
  ]
}
```

**Field rules:**
- All bool fields must be JSON `true` / `false` — strings (`"false"`) and numbers (`0`/`1`) are rejected
- All numeric fields must be finite — `NaN` and `Infinity` are rejected
- `temperatures` timestamps must be strictly increasing (no duplicates, no reversal)
- `temp_missing=true` requires `temp_records_complete=false` and `temperatures=[]`
- `measure_id` must exactly equal `"lifescience.coldchain.handoff.2to8c.v1"`

**When temperature data is absent for a leg:**
```json
"temperatures": [],
"temp_missing": true,
"temp_records_complete": false
```

**Optional fields** (may be omitted):
- `handoff_from`, `handoff_to` — leg endpoints (shown in output; null/absent allowed)
- `pallet_id`, `logger_id` — tracking identifiers (stored, not evaluated)

See `UPGRADE_TO_REAL_DATA.md` for the complete field specification with types,
constraints, and sample JSONs (valid / missing-field / type-error).

---

## Samples and expected outcomes

| File | Expected outcome | Scenario |
|------|-----------------|----------|
| `sample_beta_breach_reject.json` | 🔴 REJECT | d_plus = 3.5 > beta (2.0) |
| `sample_chain_stop_demo.json` (default) | 🟡 HOLD at leg 2, leg 3 skipped | Seal broken mid-chain |
| `sample_chain_stop_demo.json --continue-after-fail` | HOLD + RELEASE | All legs evaluated |
| `sample_invalid_empty_temps.json` | INPUT ERROR, exit 1 | `temperatures=[]` with `temp_missing=false` |
| `sample_invalid_negatives.json` | INPUT ERROR, exit 1 | `duration_minutes=-10` |
| `sample_invalid_timestamps.json` | INPUT ERROR, exit 1 | Unsorted timestamps |
| `sample_invalid_evidence.json` | WARN to stderr + 🟡 HOLD | `evidence_ok=false` but all sub-flags true |

The built-in demo (`python poc_handoff_pde.py` with no arguments) runs three scenarios
from code-embedded data — no JSON file required.

---

## Red-Team-Style Scenario Testing

This PoC includes red-team-style scenario testing for input tampering and integrity failures.
This is not a claim of production-grade penetration testing, cryptographic security audit,
or regulatory certification.

The current scope focuses on PoC-level validation of tampering and integrity scenarios,
specifically the validation boundary at the parsed JSON dict layer.

---

### At a glance

- 30 scenarios documented
- 25 scenarios covered by tests
- 18 blocked before evaluation
- 2 HOLD
- 3 REJECT
- 2 QA_REVIEW
- 1 Known Gap
- 4 Out of Scope

Most malformed or type-coercion attacks are blocked before evaluation.
Contradictory evidence produces HOLD rather than bypass.
A downstream clean leg cannot mask an upstream failure.
Fabricated but structurally valid data remains a Known Gap.

See `RED_TEAM_REPORT.md` for full details and `SECURITY_DESIGN.md` for boundary notes.

---

## What this PoC does NOT claim

- **Regulatory compliance** — thresholds must be validated per-product and per-lane
- **Attribution** — responsibility assignment is out of scope
- **Real-time operation** — this evaluates recorded data, not live sensor streams
- **Production readiness** — see `UPGRADE_TO_REAL_DATA.md` for the graduation checklist
- **Production-grade penetration testing** — the red-team-style scenario testing above covers the PoC's input validation boundary only
