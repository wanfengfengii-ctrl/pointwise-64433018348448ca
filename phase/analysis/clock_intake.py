"""Intake of V2 station clock pieces into the center catalog."""
from phase.analysis.clock_catalog import ClockCatalog
from phase.station.clock_artifact import decode_clock_artifact
from phase.trials.registry import TrialRegistry
from phase.trials.stations import StationSource, verify_station_source


def accept_clock(
    registry: TrialRegistry, catalog: ClockCatalog, payload: str, submitted: StationSource,
) -> str:
    artifact = decode_clock_artifact(payload)
    trial = registry.get_trial(artifact.trial_id)
    source = verify_station_source(trial.stations, artifact.source, submitted)
    # Every nominal observation time and the clock-adjusted actual time must be
    # expressible inside the registered trial domain.
    domain = trial.domain
    for clause in artifact.evidence.clauses:
        domain.require_time(clause.time)
    return catalog.store(artifact, source)
