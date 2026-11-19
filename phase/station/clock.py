"""V2 station clock pieces: fixed-trial calibration with a bounded clock offset.

A clock piece extends the V1 station package with a closed rational clock-offset
budget ``[low, high]`` (seconds). Every reading of one station shares one unknown
clock offset; its actual observation time is the nominal time plus that offset
and must stay inside the registered trial time domain.
"""
from dataclasses import dataclass
from fractions import Fraction

from phase.observations.revisions import RevisionRef
from phase.station.clock_evidence import ClockEvidence
from phase.trials.identities import TrialId
from phase.trials.rational import decode_rational, encode_rational, parse_rational
from phase.trials.stations import StationSource


CLOCK_VERSION = 2


@dataclass(frozen=True)
class ClockBounds:
    """Closed rational interval of admissible station clock offsets in seconds."""
    low: Fraction
    high: Fraction

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError('clock budget lower endpoint exceeds upper endpoint')


def make_clock_bounds(clock_bounds) -> ClockBounds:
    if not isinstance(clock_bounds, (tuple, list)) or len(clock_bounds) != 2:
        raise ValueError('clock budget needs two ordered endpoints')
    low, high = parse_rational(clock_bounds[0]), parse_rational(clock_bounds[1])
    return ClockBounds(low, high)


def encode_clock_bounds(bounds: ClockBounds) -> list:
    return [encode_rational(bounds.low), encode_rational(bounds.high)]


def decode_clock_bounds(raw: object) -> ClockBounds:
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError('clock budget encoding must be two endpoints')
    bounds = ClockBounds(decode_rational(raw[0]), decode_rational(raw[1]))
    if bounds.low > bounds.high:
        raise ValueError('clock budget lower endpoint exceeds upper endpoint')
    return bounds


@dataclass(frozen=True)
class ClockArtifact:
    """Immutable V2 station clock piece.

    It binds a fixed trial, station registration source, explicit revision
    references, the rational clock-offset budget and the calibration evidence
    (the selected observation arcs and their nominal times).
    """
    artifact_id: str
    trial_id: TrialId
    source: StationSource
    revisions: tuple[RevisionRef, ...]
    clock_bounds: ClockBounds
    evidence: ClockEvidence
