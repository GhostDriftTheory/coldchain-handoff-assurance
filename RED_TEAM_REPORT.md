# Red-Team-Style Scenario Testing Report

**Scope:** `poc_handoff_pde.py` — Cold-Chain Handoff Quality Judgement PoC  
**Profile:** `lifescience_2to8c_standard_handoff v1`

This document records the input-tampering and integrity-failure scenarios exercised
against this PoC. Testing is red-team-style scenario testing at PoC scope — it is
**not** a claim of production-grade penetration testing, cryptographic security audit,
or regulatory certification.

---

## Executive Summary

This PoC exercised 30 red-team-style tampering and integrity scenarios against the cold-chain handoff judgement flow.

Key outcomes:
- 18 scenarios were blocked before evaluation
- 2 scenarios resulted in HOLD
- 3 scenarios resulted in REJECT
- 2 scenarios resulted in QA_REVIEW
- 1 scenario remains a Known Gap
- 4 scenarios are explicitly Out of Scope

Overall, the current PoC demonstrates strong resistance to input coercion, structural tampering, ordering errors, evidence contradictions, and downstream masking attempts within its file-based validation boundary.

---

## Scope and Limitations

This report covers:
- Input validation attacks detectable at the Python dict boundary (post-JSON-parse)
- Boolean coercion attacks exploitable via serialisation pipelines
- Timestamp ordering and duplication attacks
- Evidence flag contradiction attempts
- Chain-masking attempts via a clean downstream leg after an upstream failure
- Threshold boundary edge cases

This report does **not** cover (see Out of Scope table below):
- Tampering before `json.load()` (encoding tricks, crafted bytes)
- Cryptographic authenticity of logger data
- API-layer authentication or rate-limiting
- Hardware or sensor authenticity verification

---

## Full Attack Scenario Table

| Scenario ID | Attack / Tampering Vector | Example Input | Expected Behaviour | Observed Behaviour | Outcome Class | Classification | Notes |
|-------------|--------------------------|---------------|--------------------|--------------------|---------------|----------------|-------|
| SC-001 | Bool string coercion — `"false"` for evidence flag | `evidence_ok: "false"` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | `bool("false")` is `True` in Python; `_strict_bool()` rejects non-bool types |
| SC-002 | Bool string coercion — `"true"` for evidence flag | `seal_intact: "true"` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | Same coercion risk as SC-001 |
| SC-003 | Bool string coercion — `"0"` for evidence flag | `custody_complete: "0"` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | String `"0"` is truthy in Python; type-check prevents silent coercion |
| SC-004 | Bool string coercion — `"1"` for evidence flag | `logger_calibrated: "1"` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | String `"1"` is truthy in Python; type-check prevents silent coercion |
| SC-005 | Bool integer coercion — `0` (int) for evidence flag | `evidence_ok: 0` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | `bool(0)` is `False`; would silently flip flag in many pipelines |
| SC-006 | Bool integer coercion — `1` (int) for evidence flag | `seal_intact: 1` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | `bool(1)` is `True`; same risk as SC-005 |
| SC-007 | Missing `measure_id` | `measure_id` key absent | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | Prevents evaluation under an unintended profile |
| SC-008 | Mismatched `measure_id` | `measure_id: "wrong.id.v99"` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | Second check in `evaluate_product_disposition()` provides defence-in-depth |
| SC-009 | Empty `temperatures` with `temp_missing: false` | `temperatures: []`, `temp_missing: false` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | Claiming records exist while providing none would allow all-zero excursion metrics |
| SC-010 | Contradictory evidence flags — `evidence_ok: false` with all sub-flags `true` | `evidence_ok: false`, `seal_intact: true`, `custody_complete: true`, … | HOLD verdict + stderr `[WARN]` | HOLD issued; `[WARN]` written to stderr | HOLD | Covered | Logically contradictory but structurally valid; `evidence_ok: false` triggers evidence gate regardless of sub-flags; warning logged for upstream investigation |
| SC-011 | Unsorted timestamps | `temperatures: [[10, 5.0], [0, 5.1], [20, 5.2]]` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | Reversed sequence would corrupt area-under-curve excursion calculation |
| SC-012 | Duplicate timestamps | `temperatures: [[0, 5.0], [5, 5.1], [5, 5.2], [10, 5.0]]` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | Requires strictly increasing (`t[i] > t[i-1]`); equality is rejected |
| SC-013 | NaN injection — `duration_minutes` | `duration_minutes: NaN` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | `NaN > 90.0` is `False` in Python; NaN-duration would silently pass all beta checks |
| SC-014 | NaN injection — temperature value | `temperatures: [[0, NaN], [5, 5.1]]` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | Same silent-comparison risk as SC-013 |
| SC-015 | Inf injection — temperature timestamp | `temperatures: [[Infinity, 5.0]]` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | `math.isfinite()` rejects ±inf before timestamp ordering check |
| SC-016 | Inf injection — temperature value | `temperatures: [[0, Infinity]]` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | Would produce infinite excursion metrics |
| SC-017 | Negative timestamp | `temperatures: [[-1, 5.0], [5, 5.1]]` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | Negative time is not meaningful in this data model; rejected explicitly |
| SC-018 | Negative `duration_minutes` | `duration_minutes: -10` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | `duration < 0` is rejected after finite-check |
| SC-019 | Malformed temperature pair structure | `temperatures: [[0, 5.0, "extra"]]` | Blocked before evaluation | `InputValidationError` raised | Blocked before evaluation | Covered | Each entry must be a 2-element `[time, temp]` pair; wrong length rejected |
| SC-020 | Downstream clean-leg masking after upstream HOLD | HOLD at leg 2, clean leg at leg 3 | Chain stops at HOLD; leg 3 not evaluated | Leg 3 not evaluated; `stopped_early=True` | HOLD | Covered | Default `--stop-on-fail` prevents a superficially clean leg from masking an upstream failure |
| SC-021 | Downstream clean-leg masking after upstream REJECT | REJECT at leg 1, clean leg at leg 2 | Chain stops at REJECT; leg 2 not evaluated | Leg 2 not evaluated; `stopped_early=True` | REJECT | Covered | Same chain-halt behaviour; REJECT severity does not differ from HOLD re: chain stop |
| SC-022 | Threshold boundary — `d_plus` exactly at beta (`2.0`) | `peak: 10.0°C` → `d_plus = 2.0` | QA_REVIEW (not REJECT) | QA_REVIEW | QA_REVIEW | Covered | `_exceeds()` rule: `observed > threshold`; equal is not a breach; `2.0 > 2.0` is `False` |
| SC-023 | Threshold boundary — `d_plus` just above beta (`2.1`) | `peak: 10.1°C` → `d_plus = 2.1` | REJECT | REJECT | REJECT | Covered | `2.1 > 2.0` is `True`; fatal breach confirmed |
| SC-024 | Threshold boundary — `duration_minutes` exactly at beta (`90.0`) | `duration_minutes: 90.0` | QA_REVIEW (not REJECT) | QA_REVIEW | QA_REVIEW | Covered | Same `_exceeds()` rule; `90.0 > 90.0` is `False` |
| SC-025 | Threshold boundary — `duration_minutes` just above beta (`90.001`) | `duration_minutes: 90.001` | REJECT | REJECT | REJECT | Covered | `90.001 > 90.0` is `True`; fatal breach confirmed |
| SC-026 | Fabricated but structurally valid data | Well-formed JSON with plausible but fabricated temperature readings, all fields correct | Passes validation; verdict depends on metric values | Passes validation; verdict issued normally | Not detectable in current PoC | Known Gap | The PoC validates structure and consistency, not data provenance. A fabricated-but-valid sequence is indistinguishable from genuine sensor data. Cryptographic logger signing would be required to close this gap (see Out of Scope). |
| SC-027 | Pre-JSON-load tampering | Byte-level manipulation, encoding tricks, malicious JSON structure before `json.load()` | Outside detection boundary | Not checked; Python's `json.load()` handles parsing | — | Out of Scope | `validate_handoff_input()` operates on an already-parsed Python dict. Pre-parse tampering is not in scope for this PoC. |
| SC-028 | Cryptographic authenticity | Absent or forged logger signatures, Ed25519 audit trail bypass | Not implemented | Not checked | — | Out of Scope | Hardware-signed logger data and cryptographic provenance chains are a future extension. See `UPGRADE_TO_REAL_DATA.md`. |
| SC-029 | API boundary hardening | Authentication bypass, injection via API parameters, rate-limit exhaustion | Not implemented | Not checked | — | Out of Scope | Input is read from JSON files; no API boundary exists in this PoC. |
| SC-030 | Hardware / sensor authenticity | Physical sensor calibration attack, compromised sensor hardware | Not implemented | Not checked | — | Out of Scope | Sensor-level hardware authenticity is outside the PoC scope; requires certified hardware and audit trails. |

---

## Summary Counts

| Category | Count |
|----------|-------|
| Total scenarios documented | 30 |
| Scenarios with corresponding test coverage | 25 |
| Blocked before evaluation (`InputValidationError`) | 18 |
| Resulted in HOLD | 2 |
| Resulted in REJECT | 3 |
| Resulted in QA_REVIEW | 2 |
| Passes but flagged | 0 |
| Not detectable in current PoC (Known Gap) | 1 |
| Out of Scope | 4 |

---

## Most Important Current Limitation

### SC-026 — Fabricated but structurally valid data

The current PoC cannot distinguish genuine logger data from fabricated but structurally valid input.

If an attacker provides internally consistent JSON with plausible values, the PoC will issue a normal verdict based on those values.

This means the current PoC validates structure and internal consistency, but not data provenance or hardware authenticity.

Closing this gap would require cryptographic logger signing and chain-of-custody attestation. That is not implemented in the current PoC.


---

## Out-of-Scope Detail

| Scenario | Why Out of Scope |
|----------|-----------------|
| SC-027 Pre-JSON-load tampering | The validation boundary is the parsed Python dict; JSON parsing itself is delegated to the standard library |
| SC-028 Cryptographic authenticity | Requires hardware-rooted signing infrastructure beyond the PoC's single-file, no-dependency design |
| SC-029 API boundary hardening | No API layer exists; input is file-based |
| SC-030 Hardware / sensor authenticity | Requires certified hardware and calibration audit trails at the physical layer |

---

## Column Definitions

| Column | Meaning |
|--------|---------|
| **Outcome Class** | What the PoC does with this input: `Blocked before evaluation` (InputValidationError raised, no verdict); `HOLD` / `REJECT` / `QA_REVIEW` (valid input, quality verdict issued); `Not detectable in current PoC` (passes through silently) |
| **Classification** | `Covered` — scenario is validated by at least one pytest case; `Known Gap` — in scope but not currently detectable; `Out of Scope` — intentionally outside the PoC boundary |

---

> This PoC includes red-team-style scenario testing for input tampering and integrity failures.  
> This is not a claim of production-grade penetration testing.  
> The current scope focuses on PoC-level validation of tampering and integrity scenarios.
