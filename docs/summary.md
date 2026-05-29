# Cold-Chain Handoff Quality Judgement — PoC Summary

**scenario:** chain停止デモ — 区間2のHOLDで後続評価を停止（--stop-on-fail デフォルト動作）  
**generated:** 2026-03-27 02:57 UTC  
**profile:** `lifescience_2to8c_standard_handoff` v1  (PoC starter / NOT regulatory)  
**profile_digest:** `bed4948bf11ae4383874acdbb18da549...`  
**ruleset_digest:** `27a50d185514658cedc63b76e0c9d683...`  

---

## Verdict Summary

| # | Leg | Verdict | Reason |
|---|-----|---------|--------|
| 1 | 出荷元 (Shipper-A) → 一次輸送業者 (Carrier-X) | ✅ RELEASE | 証拠完備・閾値内 |
| 2 | 一次輸送業者 (Carrier-X) → 中継拠点 (Hub-B) | 🟡 HOLD | 証拠欠損 |

---

## Leg Detail

### [1] 出荷元 (Shipper-A) → 一次輸送業者 (Carrier-X)

**Verdict:** ✅ RELEASE  
**Reason:** 証拠完備・閾値内  
**shipment_id:** `SHIP-CHAINSTOP-001`  **charter_id:** `CHARTER-LS-2TO8-POC-001`  

| Metric | Observed | Alpha | Beta | Status |
|--------|----------|-------|------|--------|
| d_plus [°C] | 0.000 | 0.30 | 2.00 | OK ✓ |
| d_minus [°C] | 0.000 | 0.30 | 2.00 | OK ✓ |
| a_plus [°C·min] | 0.000 | 3.00 | 30.00 | OK ✓ |
| a_minus [°C·min] | 0.000 | 3.00 | 30.00 | OK ✓ |
| gap [min] | 5.000 | 10.00 | 45.00 | OK ✓ |
| duration [min] | 18.000 | 30.00 | 90.00 | OK ✓ |

Evidence: seal=OK custody=OK logger=OK temp_rec=OK  

### [2] 一次輸送業者 (Carrier-X) → 中継拠点 (Hub-B)

**Verdict:** 🟡 HOLD  
**Reason:** 証拠欠損  
**shipment_id:** `SHIP-CHAINSTOP-001`  **charter_id:** `CHARTER-LS-2TO8-POC-001`  

| Metric | Observed | Alpha | Beta | Status |
|--------|----------|-------|------|--------|
| d_plus [°C] | 0.000 | 0.30 | 2.00 | OK ✓ |
| d_minus [°C] | 0.000 | 0.30 | 2.00 | OK ✓ |
| a_plus [°C·min] | 0.000 | 3.00 | 30.00 | OK ✓ |
| a_minus [°C·min] | 0.000 | 3.00 | 30.00 | OK ✓ |
| gap [min] | 8.000 | 10.00 | 45.00 | OK ✓ |
| duration [min] | 25.000 | 30.00 | 90.00 | OK ✓ |

Evidence: seal=❌ custody=OK logger=OK temp_rec=OK  

---

## Investigation Anchor

- **Last good leg:** [1] 出荷元 (Shipper-A) → 一次輸送業者 (Carrier-X)
- **First failed leg:** [2] 一次輸送業者 (Carrier-X) → 中継拠点 (Hub-B)
- Downstream legs were not evaluated because `--stop-on-fail` halted the chain at leg [2].

---

> PoC starter profile — NOT a regulatory claim.
