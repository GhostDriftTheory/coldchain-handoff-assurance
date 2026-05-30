#!/usr/bin/env python3
"""
poc_handoff_pde.py
==================
Medical 2-8°C Cold-Chain — Handoff Quality Judgement PoC

Demonstrates reproducible quality judgement at each handoff point:
  RELEASE  — evidence complete, all metrics within profile
  HOLD     — evidence gap (missing records / broken seal)
  QA_REVIEW— temperature excursion in the gray zone (alpha < observed <= beta)
  REJECT   — fatal breach (observed > beta threshold)

The chain halts at the first non-RELEASE leg by default (--stop-on-fail).

Usage
-----
  python poc_handoff_pde.py                                     # built-in 3-scenario demo
  python poc_handoff_pde.py samples/sample_beta_breach_reject.json
  python poc_handoff_pde.py --chain samples/sample_chain_stop_demo.json
  python poc_handoff_pde.py --chain samples/sample_chain_stop_demo.json --continue-after-fail
  python poc_handoff_pde.py --chain samples/sample_chain_stop_demo.json --write-summary

Note
----
Uses a conservative starter profile (lifescience_2to8c_standard_handoff v1).
NOT a regulatory claim. Validate per-product/per-lane before production use.
"""

from __future__ import annotations

import argparse, json, math, sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


# ===========================================================================
# SECTION 0 — Input validation
# ===========================================================================

class InputValidationError(ValueError):
    """Raised when a handoff input is structurally or logically invalid.

    Distinct from HOLD / REJECT verdicts:
      HOLD / REJECT        — valid input; evidence gap or threshold breach.
      InputValidationError — input is malformed and cannot be evaluated.

    In any production context, treat this as an upstream data-quality failure,
    not as a quality verdict on the shipment.
    """


def _strict_bool(val: object, field: str, sid: str) -> bool:
    """Accept only JSON true / false (Python bool).

    Strings ("false", "true") and numbers (0, 1) are rejected — these would
    silently coerce to True via bool(), masking data pipeline errors.
    """
    if not isinstance(val, bool):
        raise InputValidationError(
            f"{field} must be JSON true or false "
            f"(strings and numbers are not accepted, got {val!r}) "
            f"(shipment_id={sid})")
    return val


def _strict_finite(val: object, field: str, sid: str) -> float:
    """Convert to float, rejecting NaN, ±inf, and bool-typed values.

    Python bools are a subclass of int and would silently become 1.0/0.0;
    we catch them explicitly.
    """
    if isinstance(val, bool):
        raise InputValidationError(
            f"{field} must be a finite number; "
            f"boolean {val!r} is not valid here (shipment_id={sid})")
    try:
        fv = float(val)
    except (TypeError, ValueError):
        raise InputValidationError(
            f"{field} must be numeric, got {val!r} (shipment_id={sid})")
    if not math.isfinite(fv):
        raise InputValidationError(
            f"{field} must be a finite number (not NaN/inf), "
            f"got {val!r} (shipment_id={sid})")
    return fv


def validate_handoff_input(h: dict) -> None:  # noqa: C901
    """Validate one handoff dict before parsing.

    Raises InputValidationError when the input is malformed. See docs/SECURITY_DESIGN.md
    for the full list of conditions checked.

    Emits a stderr WARNING (does not raise) when evidence_ok=false but all
    individual sub-flags are true — the verdict will still be HOLD; no bypass
    is possible.
    """
    # shipment_id
    sid = h.get("shipment_id", "")
    if not sid or not str(sid).strip():
        raise InputValidationError("shipment_id is missing or empty")
    sid = str(sid).strip()

    # measure_id (required; must match the profile's expected constant)
    _expected_mid = "lifescience.coldchain.handoff.2to8c.v1"
    mid_raw = h.get("measure_id")
    if mid_raw is None:
        raise InputValidationError(
            f"measure_id is missing; expected {_expected_mid!r} (shipment_id={sid})")
    if mid_raw != _expected_mid:
        raise InputValidationError(
            f"measure_id mismatch: expected {_expected_mid!r}, "
            f"got {mid_raw!r} (shipment_id={sid})")

    # handoff_from / handoff_to: absent/null is allowed; empty string is not
    for key in ("handoff_from", "handoff_to"):
        val = h.get(key)
        if val is not None and str(val).strip() == "":
            raise InputValidationError(
                f"{key} is empty string — supply a non-empty value or omit the key "
                f"(shipment_id={sid})")

    # duration_minutes
    dur_raw = h.get("duration_minutes")
    if dur_raw is None:
        raise InputValidationError(f"duration_minutes is missing (shipment_id={sid})")
    dur = _strict_finite(dur_raw, "duration_minutes", sid)
    if dur < 0:
        raise InputValidationError(
            f"duration_minutes must be >= 0, got {dur} (shipment_id={sid})")

    # max_temp_sample_gap_minutes
    gap = _strict_finite(
        h.get("max_temp_sample_gap_minutes", 0),
        "max_temp_sample_gap_minutes", sid)
    if gap < 0:
        raise InputValidationError(
            f"max_temp_sample_gap_minutes must be >= 0, got {gap} (shipment_id={sid})")

    # temperatures
    temps_raw = h.get("temperatures")
    if temps_raw is None:
        raise InputValidationError(f"temperatures key is missing (shipment_id={sid})")
    if not isinstance(temps_raw, list):
        raise InputValidationError(
            f"temperatures must be a list, got {type(temps_raw).__name__} "
            f"(shipment_id={sid})")

    times: List[float] = []
    for i, entry in enumerate(temps_raw):
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise InputValidationError(
                f"temperatures[{i}] must be a [time, temp] pair, got {entry!r} "
                f"(shipment_id={sid})")
        t_raw, v_raw = entry
        t_f = _strict_finite(t_raw, f"temperatures[{i}][0] (time)", sid)
        if t_f < 0:
            raise InputValidationError(
                f"temperatures[{i}][0] (time) must be >= 0, got {t_f} "
                f"(shipment_id={sid})")
        _strict_finite(v_raw, f"temperatures[{i}][1] (temp °C)", sid)
        times.append(t_f)

    for i in range(1, len(times)):
        if times[i] <= times[i - 1]:
            raise InputValidationError(
                f"temperatures timestamps must be strictly increasing; "
                f"index {i}: t={times[i]} <= t[{i-1}]={times[i-1]} "
                f"(unsorted or duplicate) (shipment_id={sid})")

    # bool fields: evidence flags
    _BOOL_FIELDS = {
        "evidence_ok": True, "custody_complete": True, "seal_intact": True,
        "logger_calibrated": True, "temp_records_complete": True,
    }
    parsed: dict = {}
    for field, default in _BOOL_FIELDS.items():
        parsed[field] = _strict_bool(h.get(field, default), field, sid)

    temp_missing = _strict_bool(h.get("temp_missing", False), "temp_missing", sid)

    # temp_missing / temp_records_complete consistency
    if not temp_missing and len(temps_raw) == 0:
        raise InputValidationError(
            f"temp_missing=false but temperatures is empty — "
            f"set temp_missing=true or provide actual records (shipment_id={sid})")
    if temp_missing and parsed["temp_records_complete"]:
        raise InputValidationError(
            f"temp_missing=true and temp_records_complete=true are contradictory "
            f"(shipment_id={sid})")

    # evidence_ok contradiction warning
    all_sub_ok = (
        parsed["custody_complete"] and parsed["seal_intact"]
        and parsed["logger_calibrated"]
        and (not temp_missing) and parsed["temp_records_complete"]
    )
    if not parsed["evidence_ok"] and all_sub_ok:
        print(
            f"[WARN] evidence_ok=false but all sub-flags are true (shipment_id={sid}). "
            f"Contradictory input — leg will receive HOLD. "
            f"Check upstream data source.",
            file=sys.stderr,
        )


# ===========================================================================
# SECTION 1 — Generic PDE Core
# ===========================================================================

class Containment(str, Enum):
    FLOW = "FLOW"; ISOLATE = "ISOLATE"; DISCARD = "DISCARD"

class ReasonClass(str, Enum):
    AUTO_RELEASE = "AUTO_RELEASE"; EVIDENCE_GAP = "EVIDENCE_GAP"
    QUALITY_REVIEW = "QUALITY_REVIEW"; FATAL_BREACH = "FATAL_BREACH"

class DispositionLabel(str, Enum):
    RELEASE = "RELEASE"; HOLD = "HOLD"; QA_REVIEW = "QA_REVIEW"; REJECT = "REJECT"


@dataclass(frozen=True)
class ThresholdBand:
    d_plus: float; d_minus: float; a_plus: float; a_minus: float
    max_gap_minutes: float; duration_minutes: float

@dataclass(frozen=True)
class RequirementFlags:
    require_temp_records: bool = True; require_logger_calibrated: bool = True
    require_custody_complete: bool = True; require_seal_intact: bool = True
    require_measure_id: bool = True

@dataclass(frozen=True)
class ProductProfile:
    profile_id: str; profile_version: str
    temperature_lower_c: float; temperature_upper_c: float
    alpha: ThresholdBand; beta: ThresholdBand
    requirements: RequirementFlags; notes: str = ""

    def validate(self):
        for k in asdict(self.alpha):
            if getattr(self.alpha, k) > getattr(self.beta, k):
                raise ValueError(f"alpha.{k} exceeds beta.{k}")

@dataclass(frozen=True)
class CharterBinding:
    charter_id: str; required_measure_id: str
    disposition_ruleset_digest: str; required_profile_id: str; required_profile_digest: str

@dataclass(frozen=True)
class EvidenceStatus:
    """Evidence flags for one handoff leg.

    Note: receiver authentication is tracked in the input schema for forward
    compatibility but is not evaluated in this PoC's disposition logic.
    See docs/UPGRADE_TO_REAL_DATA.md §Future Extensions.
    """
    ok: bool; custody_complete: bool; seal_intact: bool
    logger_calibrated: bool; temp_records_complete: bool

@dataclass(frozen=True)
class UwpSnapshot:
    shipment_id: str; measure_id: str; duration_minutes: float
    temperatures: Sequence[Tuple[float, float]]; temp_missing: bool
    max_temp_sample_gap_minutes: float
    handoff_from: Optional[str] = None; handoff_to: Optional[str] = None
    pallet_id: Optional[str] = None; logger_id: Optional[str] = None

@dataclass(frozen=True)
class PDEMetrics:
    d_plus: float; d_minus: float; a_plus: float; a_minus: float; sample_count: int
    max_gap_minutes: float; duration_minutes: float
    evidence_ok: bool; custody_complete: bool; seal_intact: bool
    logger_calibrated: bool; temp_records_complete: bool

@dataclass(frozen=True)
class DispositionDecision:
    containment: Containment; reason_class: ReasonClass; label: DispositionLabel
    profile_id: str; charter_id: str; shipment_id: str; measure_id: str
    metrics: PDEMetrics; ruleset_digest: str; profile_digest: str
    handoff_from: Optional[str] = None; handoff_to: Optional[str] = None

    def canonical_payload(self) -> dict:
        return {
            "containment": self.containment.value,
            "reason_class": self.reason_class.value,
            "label": self.label.value,
            "profile_id": self.profile_id,
            "charter_id": self.charter_id,
            "shipment_id": self.shipment_id,
            "measure_id": self.measure_id,
            "metrics": asdict(self.metrics),
            "handoff_from": self.handoff_from,
            "handoff_to": self.handoff_to,
            "ruleset_digest": self.ruleset_digest,
            "profile_digest": self.profile_digest,
        }


# ---------------------------------------------------------------------------
# Threshold comparison — centralised rule
# ---------------------------------------------------------------------------

def _exceeds(observed: float, threshold: float) -> bool:
    """Return True when observed strictly exceeds the threshold.

    Comparison rule: observed > threshold → breach / not-auto-release.
    Equal to threshold is NOT a breach — the borderline point stays in zone.

    This function is the single source of truth for all alpha/beta comparisons
    in _fatal() and _autorel(). Centralising it makes the rule auditable and
    ensures consistent behaviour across all metrics.

    IEEE-754 note: beta thresholds (2.0, 30.0, 90.0°C) are exactly representable
    and comparisons are stable. Alpha thresholds (0.3°C) may interact with sensor
    rounding near the boundary. Production deployments should apply appropriate
    measurement margins. See docs/SECURITY_DESIGN.md §Known Boundaries.
    """
    return observed > threshold


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cd(obj: object) -> str:
    return sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        .encode()
    ).hexdigest()

def _pd(profile: ProductProfile) -> str:
    return _cd(asdict(profile))

def _rd(profile: ProductProfile) -> str:
    return _cd({
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "temperature_lower_c": profile.temperature_lower_c,
        "temperature_upper_c": profile.temperature_upper_c,
        "alpha": asdict(profile.alpha),
        "beta": asdict(profile.beta),
        "requirements": asdict(profile.requirements),
    })

def _excursion(
    temps: Sequence[Tuple[float, float]], lo: float, hi: float
) -> Tuple[float, float, float, float, int]:
    s = sorted((float(t), float(v)) for t, v in temps)
    if not s:
        return 0., 0., 0., 0., 0
    dp = dm = ap = am = 0.
    for i, (t, v) in enumerate(s):
        ep = max(0., v - hi); em = max(0., lo - v)
        dp = max(dp, ep);    dm = max(dm, em)
        if i > 0:
            dt = max(0., t - s[i - 1][0])
            ap += ep * dt;   am += em * dt
    return dp, dm, ap, am, len(s)

def _metrics(snap: UwpSnapshot, ev: EvidenceStatus, prof: ProductProfile) -> PDEMetrics:
    dp, dm, ap, am, n = _excursion(
        snap.temperatures, prof.temperature_lower_c, prof.temperature_upper_c)
    return PDEMetrics(
        dp, dm, ap, am, n,
        float(snap.max_temp_sample_gap_minutes),
        float(snap.duration_minutes),
        bool(ev.ok), bool(ev.custody_complete), bool(ev.seal_intact),
        bool(ev.logger_calibrated),
        (not snap.temp_missing) and bool(ev.temp_records_complete),
    )

def _ev_gate(m: PDEMetrics, prof: ProductProfile) -> bool:
    r = prof.requirements
    if not m.evidence_ok:                                       return False
    if r.require_temp_records and not m.temp_records_complete:  return False
    if r.require_logger_calibrated and not m.logger_calibrated: return False
    if r.require_custody_complete and not m.custody_complete:   return False
    if r.require_seal_intact and not m.seal_intact:             return False
    return True

def _fatal(m: PDEMetrics, prof: ProductProfile) -> bool:
    """True if any metric exceeds the beta (fatal) threshold."""
    b = prof.beta
    if _exceeds(m.d_plus, b.d_plus) or _exceeds(m.d_minus, b.d_minus):
        return True
    if _exceeds(m.a_plus, b.a_plus) or _exceeds(m.a_minus, b.a_minus):
        return True
    if _exceeds(m.max_gap_minutes, b.max_gap_minutes) or _exceeds(m.duration_minutes, b.duration_minutes):
        return True
    if prof.requirements.require_seal_intact and not m.seal_intact:
        return True
    return False

def _autorel(m: PDEMetrics, prof: ProductProfile) -> bool:
    """True if all metrics are within the alpha (auto-release) threshold."""
    a = prof.alpha
    return not (
        _exceeds(m.d_plus, a.d_plus) or _exceeds(m.d_minus, a.d_minus) or
        _exceeds(m.a_plus, a.a_plus) or _exceeds(m.a_minus, a.a_minus) or
        _exceeds(m.max_gap_minutes, a.max_gap_minutes) or
        _exceeds(m.duration_minutes, a.duration_minutes)
    )

_LBL = {
    (Containment.FLOW,    ReasonClass.AUTO_RELEASE):   DispositionLabel.RELEASE,
    (Containment.ISOLATE, ReasonClass.EVIDENCE_GAP):   DispositionLabel.HOLD,
    (Containment.ISOLATE, ReasonClass.QUALITY_REVIEW): DispositionLabel.QA_REVIEW,
    (Containment.DISCARD, ReasonClass.FATAL_BREACH):   DispositionLabel.REJECT,
}


def evaluate_product_disposition(
    snap: UwpSnapshot, ev: EvidenceStatus,
    prof: ProductProfile, charter: CharterBinding,
) -> DispositionDecision:
    """Evaluate one handoff leg against the profile and charter.

    Decision order (fixed, auditable):
      1. Evidence gap  → HOLD
      2. Fatal breach  → REJECT
      3. Auto-release  → RELEASE
      4. Gray zone     → QA_REVIEW
    """
    prof.validate()
    pd = _pd(prof); rd = _rd(prof)
    if charter.required_profile_id != prof.profile_id:
        raise ValueError("profile_id mismatch")
    if charter.required_profile_digest != pd:
        raise ValueError("profile_digest mismatch")
    if charter.disposition_ruleset_digest != rd:
        raise ValueError("ruleset_digest mismatch")
    if prof.requirements.require_measure_id and snap.measure_id != charter.required_measure_id:
        raise InputValidationError(
            f"measure_id mismatch: expected {charter.required_measure_id!r}, "
            f"got {snap.measure_id!r}")

    m = _metrics(snap, ev, prof)
    if not _ev_gate(m, prof):   c, r = Containment.ISOLATE, ReasonClass.EVIDENCE_GAP
    elif _fatal(m, prof):       c, r = Containment.DISCARD, ReasonClass.FATAL_BREACH
    elif _autorel(m, prof):     c, r = Containment.FLOW,    ReasonClass.AUTO_RELEASE
    else:                       c, r = Containment.ISOLATE, ReasonClass.QUALITY_REVIEW

    return DispositionDecision(
        c, r, _LBL[(c, r)], prof.profile_id, charter.charter_id,
        snap.shipment_id, snap.measure_id, m, rd, pd,
        snap.handoff_from, snap.handoff_to,
    )


# ===========================================================================
# SECTION 2 — Life-science vertical: 2–8°C standard profile
# ===========================================================================

MEASURE_ID = "lifescience.coldchain.handoff.2to8c.v1"

def _build_profile() -> ProductProfile:
    """Build the PoC starter profile for 2–8°C cold-chain handoffs.

    Thresholds are conservative starting values — validate per-product
    and per-lane before any production use.
    """
    p = ProductProfile(
        profile_id="lifescience_2to8c_standard_handoff",
        profile_version="v1",
        temperature_lower_c=2.0,
        temperature_upper_c=8.0,
        alpha=ThresholdBand(
            d_plus=0.3, d_minus=0.3,
            a_plus=3.0, a_minus=3.0,
            max_gap_minutes=10.0, duration_minutes=30.0,
        ),
        beta=ThresholdBand(
            d_plus=2.0, d_minus=2.0,
            a_plus=30.0, a_minus=30.0,
            max_gap_minutes=45.0, duration_minutes=90.0,
        ),
        requirements=RequirementFlags(),
        notes="PoC starter — 2-8°C handoff. NOT regulatory.",
    )
    p.validate()
    return p

def _build_charter(charter_id: str, profile: ProductProfile) -> CharterBinding:
    return CharterBinding(
        charter_id, MEASURE_ID,
        _rd(profile), profile.profile_id, _pd(profile),
    )

def evaluate_handoff(
    snap: UwpSnapshot, ev: EvidenceStatus,
    charter_id: str = "CHARTER-LS-2TO8-POC-001",
) -> DispositionDecision:
    p = _build_profile()
    return evaluate_product_disposition(snap, ev, p, _build_charter(charter_id, p))


# ===========================================================================
# SECTION 3 — Chain runner
# ===========================================================================

@dataclass
class HandoffResult:
    seq: int
    handoff_from: str
    handoff_to: str
    decision: DispositionDecision

    @property
    def passed(self) -> bool:
        return self.decision.label == DispositionLabel.RELEASE


def _parse(h: dict) -> Tuple[UwpSnapshot, EvidenceStatus]:
    """Validate and parse one raw handoff dict into typed objects."""
    validate_handoff_input(h)
    snap = UwpSnapshot(
        shipment_id=h["shipment_id"],
        measure_id=h["measure_id"],
        duration_minutes=float(h["duration_minutes"]),
        temperatures=[tuple(t) for t in h.get("temperatures", [])],
        temp_missing=h["temp_missing"],
        max_temp_sample_gap_minutes=float(h.get("max_temp_sample_gap_minutes", 0)),
        handoff_from=h.get("handoff_from"),
        handoff_to=h.get("handoff_to"),
        pallet_id=h.get("pallet_id"),
        logger_id=h.get("logger_id"),
    )
    ev = EvidenceStatus(
        ok=h["evidence_ok"],
        custody_complete=h["custody_complete"],
        seal_intact=h["seal_intact"],
        logger_calibrated=h["logger_calibrated"],
        temp_records_complete=h["temp_records_complete"],
    )
    return snap, ev


def run_chain(
    handoffs: list,
    charter_id: str,
    stop_after_first_nonrelease: bool = True,
) -> Tuple[List[HandoffResult], bool]:
    """Evaluate a sequence of handoff legs in order.

    Parameters
    ----------
    handoffs:
        List of raw handoff dicts (each validated before evaluation).
    charter_id:
        Charter identifier for digest binding.
    stop_after_first_nonrelease:
        True (default) — halt at the first HOLD / QA_REVIEW / REJECT and skip
        remaining legs. Evaluating downstream legs after a known chain break
        would produce misleading results.
        False — evaluate all legs regardless (useful for full-chain audit).

    Returns
    -------
    results : List[HandoffResult]
        Evaluated legs in order; may be shorter than handoffs when stopped early.
    stopped_early : bool
        True if evaluation halted before the last leg.
    """
    n_total = len(handoffs)
    out: List[HandoffResult] = []
    for i, h in enumerate(handoffs, 1):
        snap, ev = _parse(h)
        d = evaluate_handoff(snap, ev, charter_id)
        r = HandoffResult(i, snap.handoff_from or "?", snap.handoff_to or "?", d)
        out.append(r)
        if stop_after_first_nonrelease and not r.passed:
            break
    return out, len(out) < n_total


def last_good(results: List[HandoffResult]) -> Optional[HandoffResult]:
    """Return the last consecutive RELEASE from the start of the chain."""
    lg = None
    for r in results:
        if r.passed:
            lg = r
        else:
            break
    return lg

def first_fail(results: List[HandoffResult]) -> Optional[HandoffResult]:
    """Return the first non-RELEASE leg in the chain, if any."""
    return next((r for r in results if not r.passed), None)


# ===========================================================================
# SECTION 4 — Display
# ===========================================================================

_BADGE = {
    DispositionLabel.RELEASE:   "[OK] RELEASE",
    DispositionLabel.HOLD:      "[!!] HOLD",
    DispositionLabel.QA_REVIEW: "[??] QA_REVIEW",
    DispositionLabel.REJECT:    "[XX] REJECT",
}
_BADGE_PRETTY = {
    DispositionLabel.RELEASE:   "✅ RELEASE",
    DispositionLabel.HOLD:      "🟡 HOLD",
    DispositionLabel.QA_REVIEW: "🟠 QA_REVIEW",
    DispositionLabel.REJECT:    "🔴 REJECT",
}
_RSN_LABEL = {
    ReasonClass.AUTO_RELEASE:   "evidence complete, all metrics within profile",
    ReasonClass.EVIDENCE_GAP:   "evidence gap — isolate for investigation",
    ReasonClass.QUALITY_REVIEW: "alpha-zone excursion — QA review required",
    ReasonClass.FATAL_BREACH:   "beta threshold exceeded — reject",
}
_RSN_LABEL_JP = {
    ReasonClass.AUTO_RELEASE:   "証拠完備・閾値内 → 自動通過",
    ReasonClass.EVIDENCE_GAP:   "証拠欠損 → 隔離・調査",
    ReasonClass.QUALITY_REVIEW: "閾値超過（α～β帯）→ QAレビュー必要",
    ReasonClass.FATAL_BREACH:   "致命的逸脱（β超過）→ 廃棄判定",
}
_RSN_SHORT = {
    ReasonClass.AUTO_RELEASE:   "証拠完備・閾値内",
    ReasonClass.EVIDENCE_GAP:   "証拠欠損",
    ReasonClass.QUALITY_REVIEW: "閾値超過（α～β帯）",
    ReasonClass.FATAL_BREACH:   "致命的逸脱（β超過）",
}


def _thresh_table(m: PDEMetrics, prof: ProductProfile) -> str:
    a, b = prof.alpha, prof.beta
    rows = [
        ("d_plus  [°C]",    m.d_plus,          a.d_plus,          b.d_plus),
        ("d_minus [°C]",    m.d_minus,          a.d_minus,         b.d_minus),
        ("a_plus  [°C·min]",m.a_plus,           a.a_plus,          b.a_plus),
        ("a_minus [°C·min]",m.a_minus,          a.a_minus,         b.a_minus),
        ("gap     [min]",   m.max_gap_minutes,  a.max_gap_minutes, b.max_gap_minutes),
        ("duration[min]",   m.duration_minutes, a.duration_minutes,b.duration_minutes),
    ]
    hdr = f"  {'metric':<20} {'observed':>9}  {'alpha':>7}  {'beta':>7}  status"
    sep = "  " + "-" * 64
    lines = [hdr, sep]
    for name, obs, av, bv in rows:
        if _exceeds(obs, bv):   st = "EXCEEDS BETA  ← fatal"
        elif _exceeds(obs, av): st = "exceeds alpha ← QA zone"
        else:                   st = "OK"
        lines.append(f"  {name:<20} {obs:>9.3f}  {av:>7.2f}  {bv:>7.2f}  {st}")
    return "\n".join(lines)


def _fixation_block(d: DispositionDecision) -> str:
    return "\n".join([
        "  +-- ruleset binding --------------------------------------------------",
        f"  |  profile     : {d.profile_id}",
        f"  |  digest      : {d.profile_digest[:32]}...",
        f"  |  ruleset     : {d.ruleset_digest[:32]}...",
        f"  |  charter     : {d.charter_id}",
        f"  |  shipment    : {d.shipment_id}",
        f"  |  measure     : {d.measure_id}",
        f"  |  leg         : {d.handoff_from} → {d.handoff_to}",
        "  +--------------------------------------------------------------------",
    ])


def _ev_flags(m: PDEMetrics) -> str:
    def f(v): return "OK" if v else "NG"
    return (f"seal={f(m.seal_intact)}  custody={f(m.custody_complete)}"
            f"  logger={f(m.logger_calibrated)}  temp_rec={f(m.temp_records_complete)}")


def print_results(
    results: List[HandoffResult],
    scenario_name: str = "",
    stopped_early: bool = False,
    total_legs: Optional[int] = None,
) -> None:
    """Print human-readable judgement results to stdout."""
    W = 72
    sep = "-" * W
    print()
    print("=" * W)
    print("  Cold-Chain Handoff Quality Judgement PoC")
    print("  stop / preserve / trace  —  止める・残す・辿る")
    if scenario_name:
        print(f"  scenario : {scenario_name}")
    print(f"  profile  : lifescience_2to8c_standard_handoff v1"
          f"  (PoC / NOT regulatory)")
    print("=" * W)

    prof = _build_profile()
    for r in results:
        d = r.decision
        print(f"\n[{r.seq}] {r.handoff_from}  →  {r.handoff_to}")
        print(sep)
        print(f"  verdict : {_BADGE_PRETTY[d.label]}")
        print(f"  reason  : {_RSN_LABEL_JP[d.reason_class]}")
        print()
        print(_fixation_block(d))
        print()
        print("  threshold check:")
        print(_thresh_table(d.metrics, prof))
        print(f"\n  samples : {d.metrics.sample_count} pts   {_ev_flags(d.metrics)}")
        print(f"  action  : "
              f"{'>> PASS — proceed to next leg' if r.passed else '>> STOP — isolate and investigate'}")

    if stopped_early and results:
        last = results[-1]
        skipped = (total_legs - len(results)) if total_legs else "?"
        print()
        print(sep)
        print(f"  ⚠️  CHAIN STOPPED at [{last.seq}]"
              f" {last.handoff_from} → {last.handoff_to}")
        print(f"     verdict : {_BADGE[last.decision.label]}")
        print(f"     {skipped} downstream leg(s) skipped.")
        print(f"     Use --continue-after-fail to evaluate all legs.")
        print(sep)

    print()
    print(sep)
    print("  [ trace ]  investigation anchor")
    print(sep)
    lg = last_good(results)
    ff = first_fail(results)
    if not results:
        print("  No legs evaluated.")
    elif lg is None:
        print("  All evaluated legs failed — start investigation from leg [1].")
    elif ff is None:
        qualifier = "evaluated " if stopped_early else ""
        print(f"  All {qualifier}legs PASS. "
              f"Final: [{lg.seq}] {lg.handoff_from} → {lg.handoff_to}")
    else:
        print(f"  last good : [{lg.seq}] {lg.handoff_from} → {lg.handoff_to}")
        print(f"  first fail: [{ff.seq}] {ff.handoff_from} → {ff.handoff_to}")
        print("  → Narrow investigation scope to the segment above")
    print()


# ===========================================================================
# SECTION 5 — Markdown summary report
# ===========================================================================

def build_summary_md(
    results: List[HandoffResult],
    scenario_name: str = "",
    stopped_early: bool = False,
    total_legs: Optional[int] = None,
) -> str:
    prof = _build_profile()
    pd_hex = _pd(prof); rd_hex = _rd(prof)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lg = last_good(results); ff = first_fail(results)
    a, b = prof.alpha, prof.beta

    stop_note = ""
    if stopped_early and results:
        skipped = (total_legs - len(results)) if total_legs else "?"
        stop_note = (
            f"\n> ⚠️ **Chain stopped** at leg [{results[-1].seq}]"
            f" ({results[-1].decision.label.value})."
            f" {skipped} downstream leg(s) not evaluated.  \n"
        )

    lines = [
        "# Cold-Chain Handoff Quality Judgement — PoC Summary",
        "",
        f"**scenario:** {scenario_name or '—'}  ",
        f"**generated:** {ts}  ",
        f"**profile:** `{prof.profile_id}` {prof.profile_version}"
        f"  (PoC starter / NOT regulatory)  ",
        f"**profile_digest:** `{pd_hex[:32]}...`  ",
        f"**ruleset_digest:** `{rd_hex[:32]}...`  ",
        stop_note,
        "---",
        "",
        "## Verdict Summary",
        "",
        "| # | Leg | Verdict | Reason |",
        "|---|-----|---------|--------|",
    ]
    for r in results:
        d = r.decision
        lbl = {"RELEASE": "✅ RELEASE", "HOLD": "🟡 HOLD",
               "QA_REVIEW": "🟠 QA_REVIEW", "REJECT": "🔴 REJECT"}[d.label.value]
        lines.append(
            f"| {r.seq} | {r.handoff_from} → {r.handoff_to}"
            f" | {lbl} | {_RSN_SHORT[d.reason_class]} |")
    if stopped_early and results and total_legs:
        for i in range(len(results) + 1, total_legs + 1):
            lines.append(f"| {i} | — | ⏹ Not evaluated | Chain stopped |")

    lines += ["", "---", "", "## Leg Detail", ""]

    for r in results:
        d = r.decision; m = d.metrics
        lbl = {"RELEASE": "✅ RELEASE", "HOLD": "🟡 HOLD",
               "QA_REVIEW": "🟠 QA_REVIEW", "REJECT": "🔴 REJECT"}[d.label.value]
        lines += [
            f"### [{r.seq}] {r.handoff_from} → {r.handoff_to}",
            "",
            f"**Verdict:** {lbl}  ",
            f"**Reason:** {_RSN_SHORT[d.reason_class]}  ",
            f"**shipment_id:** `{d.shipment_id}`  "
            f"**charter_id:** `{d.charter_id}`  ",
            "",
            "| Metric | Observed | Alpha | Beta | Status |",
            "|--------|----------|-------|------|--------|",
        ]
        for name, obs, av, bv in [
            ("d_plus [°C]",      m.d_plus,          a.d_plus,           b.d_plus),
            ("d_minus [°C]",     m.d_minus,          a.d_minus,          b.d_minus),
            ("a_plus [°C·min]",  m.a_plus,           a.a_plus,           b.a_plus),
            ("a_minus [°C·min]", m.a_minus,          a.a_minus,          b.a_minus),
            ("gap [min]",        m.max_gap_minutes,  a.max_gap_minutes,  b.max_gap_minutes),
            ("duration [min]",   m.duration_minutes, a.duration_minutes, b.duration_minutes),
        ]:
            if _exceeds(obs, bv):   st = "EXCEEDS BETA ⚠️"
            elif _exceeds(obs, av): st = "exceeds alpha ⚠"
            else:                   st = "OK ✓"
            lines.append(f"| {name} | {obs:.3f} | {av:.2f} | {bv:.2f} | {st} |")

        def fv(v): return "OK" if v else "❌"
        lines += [
            "",
            f"Evidence: seal={fv(m.seal_intact)} custody={fv(m.custody_complete)}"
            f" logger={fv(m.logger_calibrated)} temp_rec={fv(m.temp_records_complete)}  ",
            "",
        ]

    lines += ["---", "", "## Investigation Anchor", ""]
    if not results:
        lines.append("No legs evaluated.")
    elif lg is None:
        lines.append("⚠️ All evaluated legs have issues. Investigate from leg [1].")
    elif ff is None:
        lines.append(
            f"✅ All evaluated legs passed. "
            f"Final: [{lg.seq}] {lg.handoff_from} → {lg.handoff_to}")
    else:
        lines += [
            f"- **Last good leg:** [{lg.seq}] {lg.handoff_from} → {lg.handoff_to}",
            f"- **First failed leg:** [{ff.seq}] {ff.handoff_from} → {ff.handoff_to}",
            "- Narrow investigation to this leg.",
        ]
    lines += ["", "---", "", "> PoC starter profile — NOT a regulatory claim."]
    return "\n".join(lines)


# ===========================================================================
# SECTION 6 — Built-in demo data
# ===========================================================================

def _demo_data() -> List[Tuple[str, list]]:
    """Three built-in scenarios used when no JSON file is specified."""
    MID = MEASURE_ID

    def leg(sid, dur, temps, gap, hf, ht, ev_ok=True, cust=True, seal=True,
            cal=True, trec=True, tmiss=False, pid=None, lid=None):
        return dict(
            shipment_id=sid, measure_id=MID,
            duration_minutes=dur, temperatures=temps,
            temp_missing=tmiss, max_temp_sample_gap_minutes=gap,
            handoff_from=hf, handoff_to=ht,
            pallet_id=pid, logger_id=lid,
            evidence_ok=ev_ok, custody_complete=cust,
            seal_intact=seal, logger_calibrated=cal,
            temp_records_complete=trec,
        )

    normal = [
        leg("SHIP-N001", 18, [[0,4.5],[5,5.1],[10,5.3],[15,5.0],[18,4.8]], 5.0,
            "出荷元 (Shipper-A)", "一次輸送 (Carrier-X)"),
        leg("SHIP-N001", 22, [[0,5.0],[8,5.5],[16,5.2],[22,4.9]], 8.0,
            "一次輸送 (Carrier-X)", "中継拠点 (Hub-B)"),
        leg("SHIP-N001", 25, [[0,4.8],[6,5.0],[12,5.1],[18,5.0],[24,4.7]], 6.0,
            "中継拠点 (Hub-B)", "二次輸送 (Carrier-Y)"),
        leg("SHIP-N001", 20, [[0,5.0],[10,5.2],[20,4.9]], 10.0,
            "二次輸送 (Carrier-Y)", "納品先 (Hospital-C)"),
    ]
    ev_gap = [
        leg("SHIP-E002", 20, [[0,4.5],[10,5.0],[20,5.2]], 10.0,
            "出荷元 (Shipper-A)", "一次輸送 (Carrier-X)"),
        leg("SHIP-E002", 24, [], 0.0,
            "一次輸送 (Carrier-X)", "中継拠点 (Hub-B)",
            trec=False, tmiss=True),
        leg("SHIP-E002", 18, [[0,5.0],[9,5.3],[18,5.1]], 9.0,
            "中継拠点 (Hub-B)", "納品先 (Hospital-C)"),
    ]
    temp_ex = [
        leg("SHIP-T003", 15, [[0,4.5],[5,5.0],[10,5.2],[15,4.9]], 5.0,
            "出荷元 (Shipper-A)", "一次輸送 (Carrier-X)"),
        leg("SHIP-T003", 35,
            [[0,5.0],[5,6.2],[10,8.8],[15,9.5],[20,8.2],[25,7.0],[30,6.0],[35,5.5]], 5.0,
            "一次輸送 (Carrier-X)", "中継拠点 (Hub-B)"),
        leg("SHIP-T003", 20, [[0,5.0],[10,5.3],[20,5.1]], 10.0,
            "中継拠点 (Hub-B)", "納品先 (Hospital-C)"),
    ]
    return [
        ("All-clear — 4 legs RELEASE", normal),
        ("Evidence gap — mid-chain HOLD", ev_gap),
        ("Temperature excursion — mid-chain QA_REVIEW", temp_ex),
    ]


# ===========================================================================
# SECTION 7 — Entry point
# ===========================================================================

def _load(path: str) -> Tuple[str, list]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return Path(path).stem, data
    return data.get("scenario_name", Path(path).stem), data.get("handoffs", data)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Cold-Chain Handoff Quality Judgement PoC\n"
            "Evaluates each handoff leg and halts the chain at the first issue."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples
--------
  python poc_handoff_pde.py
      Run built-in 3-scenario demo (no JSON file needed).

  python poc_handoff_pde.py samples/sample_beta_breach_reject.json
      Evaluate a single scenario from a JSON file.

  python poc_handoff_pde.py --chain samples/sample_chain_stop_demo.json
      Evaluate a multi-leg chain; stop at first non-RELEASE (default).

  python poc_handoff_pde.py --chain samples/sample_chain_stop_demo.json --continue-after-fail
      Evaluate all legs regardless of intermediate failures.

  python poc_handoff_pde.py --chain samples/sample_chain_stop_demo.json --write-summary
      Write a Markdown summary to docs/summary.md.

Chain stop behaviour
--------------------
  Default (--stop-on-fail): halt at the first HOLD/QA_REVIEW/REJECT.
    Downstream legs are NOT evaluated — a broken chain makes their
    results unreliable.
  --continue-after-fail: evaluate all legs (for full-chain audit).
""",
    )
    ap.add_argument("json_file", nargs="?",
                    help="path to a single-scenario JSON file")
    ap.add_argument("--chain", metavar="FILE",
                    help="path to a multi-leg chain JSON file")
    ap.add_argument("--write-summary", action="store_true",
                    help="write Markdown summary to docs/summary.md")
    ap.add_argument("--charter", default="CHARTER-LS-2TO8-POC-001",
                    help="charter identifier (default: CHARTER-LS-2TO8-POC-001)")

    stop_grp = ap.add_mutually_exclusive_group()
    stop_grp.add_argument(
        "--stop-on-fail", dest="stop_on_fail", action="store_true", default=True,
        help="(default) stop chain at first non-RELEASE",
    )
    stop_grp.add_argument(
        "--continue-after-fail", dest="stop_on_fail", action="store_false",
        help="evaluate all legs even after a non-RELEASE",
    )
    args = ap.parse_args()

    if args.chain:
        pairs = [_load(args.chain)]
    elif args.json_file:
        pairs = [_load(args.json_file)]
    else:
        pairs = _demo_data()

    all_res = []
    try:
        for name, handoffs in pairs:
            results, stopped_early = run_chain(
                handoffs,
                charter_id=args.charter,
                stop_after_first_nonrelease=args.stop_on_fail,
            )
            print_results(results, scenario_name=name,
                          stopped_early=stopped_early, total_legs=len(handoffs))
            all_res.append((name, results, stopped_early, len(handoffs)))
    except InputValidationError as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.write_summary and all_res:
        name, results, stopped_early, total_legs = all_res[0]
        md = build_summary_md(results, scenario_name=name,
                              stopped_early=stopped_early, total_legs=total_legs)
        Path("docs/summary.md").write_text(md, encoding="utf-8")
        print("  → docs/summary.md written.")


if __name__ == "__main__":
    main()
