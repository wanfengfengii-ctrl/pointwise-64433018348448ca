"""Calibration evidence carried by V2 station clock pieces.

Unlike V1's precomputed linear strips, clock evidence records each selected
revision's nominal observation time and recorded closed phase arc. The center
rebuilds the lifted whole-turn branches itself, combining them with the bounded
station clock offset so that every admissible whole-turn branch is preserved.
"""
from dataclasses import dataclass
from fractions import Fraction

from phase.observations.arcs import ClosedArc, make_closed_arc
from phase.trials.rational import decode_rational, encode_rational


@dataclass(frozen=True)
class ClockClause:
    """One selected revision: its nominal time and recorded closed arc."""
    time: Fraction
    arc: ClosedArc


@dataclass(frozen=True)
class ClockEvidence:
    clauses: tuple[ClockClause, ...]


def encode_clock_evidence(evidence: ClockEvidence) -> list:
    return [
        [encode_rational(clause.time),
         encode_rational(clause.arc.start), encode_rational(clause.arc.end)]
        for clause in evidence.clauses
    ]


def decode_clock_evidence(raw: object) -> ClockEvidence:
    if not isinstance(raw, list) or not raw:
        raise ValueError('clock evidence needs at least one clause')
    clauses = []
    for entry in raw:
        if not isinstance(entry, list) or len(entry) != 3:
            raise ValueError('invalid clock evidence clause')
        time = decode_rational(entry[0])
        arc = make_closed_arc(decode_rational(entry[1]), decode_rational(entry[2]))
        clauses.append(ClockClause(time, arc))
    return ClockEvidence(tuple(clauses))
