"""Exact clock-offset feasibility solver for V2 station clock pieces.

The calibration unknowns are the common initial phase ``p`` and frequency drift
``d`` shared by every selected revision, plus one bounded clock offset per
station. Introducing the auxiliary linear term ``z_s = c_s * d`` turns the
whole-turn reading constraints and the clock budget into closed linear
half-spaces over ``(p, d, z_s)``. Fourier-Motzkin elimination removes the
auxiliary terms exactly, leaving two-dimensional calibration polygons whose
linear image is folded mod one with the V1 phase-set rules.

Drift is split at zero into non-negative and non-positive cells so the clock
budget inequalities stay linear in each cell.
"""
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations

from phase.analysis.phase_set import PhaseInterval, fold_mod_one, normalize_phase_set
from phase.station.calibration import _lifted_segments
from phase.station.clock import ClockBounds
from phase.station.clock_evidence import ClockEvidence
from phase.trials.registry import RegisteredTrial


class _Half:
    __slots__ = ('a', 'limit')

    def __init__(self, coefficients, limit):
        self.a = tuple(Fraction(value) for value in coefficients)
        self.limit = Fraction(limit)

    def excess(self, point) -> Fraction:
        return sum(c * v for c, v in zip(self.a, point)) - self.limit


def _eliminate(halves, column):
    """One Fourier-Motzkin step removing the variable at `column`."""
    def without_column(half):
        return _Half(tuple(value for index, value in enumerate(half.a) if index != column),
                     half.limit)

    positive, negative, zero = [], [], []
    for half in halves:
        coefficient = half.a[column]
        if coefficient > 0:
            positive.append(half)
        elif coefficient < 0:
            negative.append(half)
        else:
            zero.append(without_column(half))
    result = list(zero)
    for upper in positive:
        cu = upper.a[column]
        for lower in negative:
            cl = -lower.a[column]
            result.append(_Half(
                tuple(lower.a[j] * cu + upper.a[j] * cl
                      for j in range(len(upper.a)) if j != column),
                lower.limit * cu + upper.limit * cl,
            ))
    unique = {}
    for half in result:
        if all(c == 0 for c in half.a):
            if half.limit < 0:
                return None
            continue
        unique[(half.a, half.limit)] = half
    return list(unique.values())


def _feasible(halves) -> bool:
    """Exact feasibility by eliminating every variable but one, then an interval test."""
    system = halves
    for column in range(len(system[0].a) - 1, 0, -1):
        system = _eliminate(system, column)
        if system is None:
            return False
    lower = upper = None
    for half in system:
        coefficient = half.a[0]
        if coefficient == 0:
            if half.limit < 0:
                return False
            continue
        bound = half.limit / coefficient
        if coefficient > 0:
            upper = bound if upper is None else min(upper, bound)
        else:
            lower = bound if lower is None else max(lower, bound)
    return lower is None or upper is None or lower <= upper


def _polygon_vertices(halves) -> set:
    vertices = set()
    for first, second in combinations(halves, 2):
        a1, b1 = first.a
        a2, b2 = second.a
        determinant = a1 * b2 - a2 * b1
        if determinant == 0:
            continue
        phase = (first.limit * b2 - second.limit * b1) / determinant
        drift = (a1 * second.limit - a2 * first.limit) / determinant
        point = (phase, drift)
        if all(half.excess(point) <= 0 for half in halves):
            vertices.add(point)
    return vertices


@dataclass(frozen=True)
class StationClockInput:
    station: str
    bounds: ClockBounds
    evidence: ClockEvidence


def _cell_base(trial: RegisteredTrial, sign: str):
    domain = trial.domain
    low, high = domain.drift_low, domain.drift_high
    zero, one = Fraction(0), Fraction(1)
    if sign == 'pos':
        d0, d1 = max(low, zero), high
    else:
        d0, d1 = low, min(high, zero)
    return [
        _Half((one, zero, zero), one), _Half((-one, zero, zero), zero),
        _Half((zero, one, zero), d1), _Half((zero, -one, zero), -d0),
    ]


def _clock_halves(sign: str, ell_star: Fraction, u_star: Fraction):
    # z = c*d with the station offset c restricted to the effective budget.
    if sign == 'pos':
        return [_Half((Fraction(0), ell_star, Fraction(-1)), 0),
                _Half((Fraction(0), -u_star, Fraction(1)), 0)]
    return [_Half((Fraction(0), u_star, Fraction(-1)), 0),
            _Half((Fraction(0), -ell_star, Fraction(1)), 0)]


def _dedupe(systems):
    seen, unique = set(), []
    for system in systems:
        key = frozenset((half.a, half.limit) for half in system)
        if key not in seen:
            seen.add(key)
            unique.append(system)
    return unique


def _solve_cell(trial: RegisteredTrial, grouped, order, sign):
    domain = trial.domain
    systems = [_cell_base(trial, sign)]
    for station in order:
        bounds, evidence = grouped[station]
        intervals = [
            (max(bounds.low, domain.start - clause.time),
             min(bounds.high, domain.end - clause.time))
            for clause in evidence.clauses
        ]
        if any(ell > u for ell, u in intervals):
            return []
        ell_star = max(ell for ell, _ in intervals)
        u_star = min(u for _, u in intervals)
        clock = _clock_halves(sign, ell_star, u_star)
        for system in systems:
            system.extend(clock)
        for clause, (ell, u) in zip(evidence.clauses, intervals):
            time = clause.time
            v_min = time * domain.drift_low + ell * domain.drift_low
            v_sup = 1 + time * domain.drift_high + u * domain.drift_high
            branches = _lifted_segments(clause.arc, v_min, v_sup)
            next_systems = []
            for system in systems:
                for alpha, beta in branches:
                    forked = system + [
                        _Half((Fraction(1), time, Fraction(1)), beta),
                        _Half((Fraction(-1), -time, Fraction(-1)), -alpha),
                    ]
                    if _feasible(forked):
                        next_systems.append(forked)
            if not next_systems:
                return []
            systems = _dedupe(next_systems)
        # Station complete: project its (p,d,z) systems back to (p,d), then lift
        # again with a fresh auxiliary term for the next station's clock.
        projected = []
        for system in systems:
            reduced = _eliminate(system, 2)
            if reduced is not None and _polygon_vertices(reduced):
                projected.append(reduced)
        if not projected:
            return []
        systems = [
            [_Half((half.a[0], half.a[1], Fraction(0)), half.limit) for half in piece]
            for piece in _dedupe(projected)
        ]
    polygons = []
    for system in systems:
        reduced = _eliminate(system, 2)
        if reduced is not None:
            vertices = _polygon_vertices(reduced)
            if vertices:
                polygons.append(vertices)
    return polygons


def solve_clock_regions(trial: RegisteredTrial, stations: list[StationClockInput]):
    """Group same-station pieces (one shared clock) and solve both drift cells."""
    grouped, order = {}, []
    for entry in stations:
        if entry.station not in grouped:
            grouped[entry.station] = (entry.bounds, entry.evidence)
            order.append(entry.station)
        elif grouped[entry.station][0] != entry.bounds:
            raise ValueError('same station clock budgets must agree')
        else:
            bounds, evidence = grouped[entry.station]
            grouped[entry.station] = (
                bounds,
                ClockEvidence(evidence.clauses + entry.evidence.clauses),
            )
    low, high = trial.domain.drift_low, trial.domain.drift_high
    polygons = []
    if high > 0:
        polygons.extend(_solve_cell(trial, grouped, order, 'pos'))
    if low < 0:
        polygons.extend(_solve_cell(trial, grouped, order, 'neg'))
    if not polygons and high <= 0 and low >= 0:
        polygons.extend(_solve_cell(trial, grouped, order, 'pos'))
    return polygons


def project_clock_phases(trial: RegisteredTrial, stations, time: Fraction):
    """Return the canonical mod-one phase set for the common trial time."""
    polygons = solve_clock_regions(trial, stations)
    pieces: list[PhaseInterval] = []
    for vertices in polygons:
        values = [(phase + time * drift, phase < 1) for phase, drift in vertices]
        low = min(value for value, _ in values)
        high = max(value for value, _ in values)
        low_closed = any(value == low and attained for value, attained in values)
        high_closed = any(value == high and attained for value, attained in values)
        pieces.extend(fold_mod_one(low, high, low_closed, high_closed))
    return normalize_phase_set(pieces)
