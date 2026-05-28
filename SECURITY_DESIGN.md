# Security Design and Input Protection

**Scope:** `poc_handoff_pde.py` — Cold-Chain Handoff Quality Judgement PoC  
**Profile:** `lifescience_2to8c_standard_handoff v1` (PoC starter / NOT regulatory)

This document describes what the PoC actively protects against, the known boundaries
of those protections, and what falls outside the current scope.

This PoC includes red-team-style scenario testing for input tampering and integrity
failures. This is not a claim of production-grade penetration testing. The current scope
focuses on PoC-level validation of tampering and integrity scenarios. For the full attack
scenario table with observed behaviour and classification, see `RED_TEAM_REPORT.md`.

---

## What the PoC protects against

### 1. Malformed input — structural errors

The following conditions raise `InputValidationError` before any evaluation occurs.
The exception is printed as `INPUT ERROR: <reason>` and the process exits with code 1.
No quality verdict is issued.

| Condition | Error keyword |
|-----------|---------------|
| `shipment_id` missing or empty | `shipment_id` |
| `measure_id` missing | `measure_id is missing` |
| `measure_id` does not match expected value | `measure_id mismatch` |
| `handoff_from` / `handoff_to` present but empty string | `handoff_from` / `handoff_to` |
| `duration_minutes` missing, not a finite number, or < 0 | `duration_minutes` |
| `max_temp_sample_gap_minutes` not a finite number or < 0 | `max_temp_sample_gap_minutes` |
| `temperatures` not a list | `must be a list` |
| `temperatures[i]` not a `[time, temp]` pair | `pair` |
| Temperature timestamp not finite or < 0 | `temperatures[i][0]` |
| Temperature value not finite | `temperatures[i][1]` |
| Timestamps not strictly increasing (unsorted or duplicate) | `strictly increasing` |
| `temp_missing=false` with empty `temperatures` | `temp_missing=false but temperatures is empty` |
| `temp_missing=true` with `temp_records_complete=true` | `contradictory` |
| Bool field (`evidence_ok`, `seal_intact`, etc.) is a string or number | `must be JSON true or false` |

### 2. Bool field coercion attacks

All bool fields must be JSON `true` / `false` — received as Python `bool`.
Strings like `"false"` (which `bool()` converts to `True`) and integers like `0` or `1`
are rejected by `_strict_bool()`. This prevents evidence flags from being silently
inverted by a data pipeline that serialises booleans as strings.

**Affected fields:** `evidence_ok`, `custody_complete`, `seal_intact`,
`logger_calibrated`, `temp_missing`, `temp_records_complete`

### 3. Non-finite numeric values (NaN / ±inf)

All numeric inputs (`duration_minutes`, `max_temp_sample_gap_minutes`, and each
temperature timestamp and value) are checked with `math.isfinite()`.
NaN and ±infinity pass Python's `float()` conversion but produce undefined comparison
results — for example, `NaN > 30.0` evaluates to `False`, which would allow a
NaN-duration leg to pass the duration check silently.

Python booleans (`True` / `False`) are also rejected in numeric fields — they are a
subclass of `int` and would otherwise silently become `1.0` / `0.0`.

### 4. Timestamp manipulation

Temperature timestamps must be strictly increasing (`t[i] > t[i-1]`). Reversed or
duplicate timestamps are rejected. Without this check, a reversed sequence would pass
the excursion integrator (`_excursion`) unchanged but with incorrect area accumulation.

### 5. Empty temperature record with false temp_missing flag

`temp_missing=false` with `temperatures=[]` is rejected. This combination would claim
that records exist while providing no data, allowing the excursion integrator to return
all-zero metrics and pass the auto-release gate.

### 6. measure_id tampering

`measure_id` must exactly match the profile's expected constant. An absent or mismatched
`measure_id` raises `InputValidationError` at validation time. A second check in
`evaluate_product_disposition()` provides a safety net.

### 7. Ruleset pinning

Each `DispositionDecision` carries `profile_digest` and `ruleset_digest` — SHA-256
hashes of the profile and ruleset configuration. Any change to thresholds, requirements,
or profile fields changes the digest, making the binding auditable.

### 8. Chain integrity

By default (`--stop-on-fail`), evaluation halts at the first non-RELEASE leg.
Downstream legs are not evaluated. This prevents a superficially clean later leg
from obscuring an upstream failure in a summary.

---

## Contradictory input — warning, not error

When `evidence_ok=false` but all individual sub-flags (`custody_complete`,
`seal_intact`, `logger_calibrated`, `temp_records_complete`) are `true`, the input
is logically contradictory. This is not raised as an `InputValidationError` because
the verdict (HOLD) is still correct — `evidence_ok=false` fails the evidence gate
regardless of sub-flags. A `[WARN]` message is printed to `stderr` to flag the
inconsistency for upstream investigation.

---

## Known boundaries

### Floating-point precision near alpha thresholds

Beta thresholds (2.0, 30.0, 90.0) are exactly representable in IEEE-754 double
precision. Alpha thresholds — specifically `d_plus = 0.3°C` — are not exactly
representable. A temperature peak computed as `8.3 - 8.0` yields
`0.3000000000000007` rather than exactly `0.3`, which places it just above the
alpha boundary.

**Practical implication:** Measurements within a few millidegrees of the alpha
boundary may land on either side depending on sensor resolution and floating-point
rounding. The PoC documents this explicitly; production deployments should apply
appropriate margins. The comparison rule (`observed > threshold` via `_exceeds()`)
is centralised in a single function to make this auditable.

### Timestamp ordering, not provenance

The PoC validates that timestamps are strictly increasing but cannot verify that
the values themselves are authentic. A fabricated but well-ordered sequence passes
validation. Hardware-signed logger data is out of scope for this PoC.

**Known Gap (SC-026):** A payload that is structurally correct and internally
consistent — but whose temperature readings, timestamps, or evidence flags are
fabricated — will pass all validation checks and receive a verdict based solely
on the values supplied. Cryptographic logger signing would be required to close
this gap. This is an intentional out-of-scope item; see `UPGRADE_TO_REAL_DATA.md
§Future Extensions` and `RED_TEAM_REPORT.md` SC-026.

### JSON parsing boundary

`validate_handoff_input()` operates on an already-parsed Python dict. Tampering
before `json.load()` (e.g., crafted bytes, encoding tricks) is outside the
validation boundary (Out of Scope SC-027 in `RED_TEAM_REPORT.md`).

---

## Out of scope for this PoC

The items below are intentionally not addressed in this PoC. They are documented
as Out of Scope in `RED_TEAM_REPORT.md` (SC-027 through SC-030) and are not
represented as covered by the red-team-style scenario tests.

| Scenario ID | Area | Notes |
|-------------|------|-------|
| SC-027 | Pre-JSON-load tampering | The validation boundary is the parsed Python dict; JSON parsing is delegated to the standard library |
| SC-028 | Cryptographic signing / authenticity | Hardware-rooted Ed25519 signatures and provenance chains are a future extension; see `UPGRADE_TO_REAL_DATA.md` |
| SC-029 | API boundary hardening | No API layer exists; input is file-based |
| SC-030 | Hardware / sensor authenticity | Requires certified hardware and calibration audit trails at the physical layer |
| — | Attribution / liability | Responsibility assignment is out of scope |
| — | Real-time sensor streams | The PoC evaluates recorded, batch data |
| — | Database or API integration | Input is read from JSON files |
| — | Regulatory compliance | Thresholds must be validated per-product / per-lane |
| — | Receiver authentication | The field exists in the data model as a future extension point; it is not evaluated in the current disposition logic |

---

> PoC starter profile — NOT a regulatory claim.  
> See `UPGRADE_TO_REAL_DATA.md` for the production graduation checklist.  
> See `RED_TEAM_REPORT.md` for the full attack scenario table and classification.
