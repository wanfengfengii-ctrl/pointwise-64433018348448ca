"""Clock-offset (V2) source-disconnection forecasts."""
from collections.abc import Iterable

from phase.analysis.clock_catalog import ClockCatalog
from phase.analysis.clock_constraints import StationClockInput, project_clock_phases
from phase.analysis.selection import ForecastSelection
from phase.reports.ledger import PublishedReport, ReportLedger
from phase.reports.publish import publish_report
from phase.station.clock import CLOCK_VERSION
from phase.station.evidence import MAX_OBSERVATIONS
from phase.trials.identities import require_id
from phase.trials.rational import RationalInput
from phase.trials.registry import TrialRegistry


def _resolve(registry: TrialRegistry, catalog: ClockCatalog, artifact_ids: Iterable[str], time):
    if isinstance(artifact_ids, str):
        raise ValueError('expected a collection of clock piece numbers')
    ids = tuple(require_id(value, 'artifact') for value in artifact_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('select distinct fixed clock pieces')

    accepted = []
    for artifact_id in sorted(ids):
        entry = catalog.load(artifact_id)
        if entry is None:
            raise ValueError(f'unknown accepted clock piece {artifact_id!r}')
        accepted.append(entry)

    trial_id = accepted[0].artifact.trial_id
    if any(entry.artifact.trial_id != trial_id for entry in accepted):
        raise ValueError('forecast cannot mix trials')
    if any(getattr(entry.artifact, 'version', CLOCK_VERSION) != CLOCK_VERSION
           for entry in accepted):
        raise ValueError('mixed-version clock piece selection')
    if sum(len(entry.artifact.revisions) for entry in accepted) > MAX_OBSERVATIONS:
        raise ValueError('forecast may use at most six observation revisions')

    trial = registry.get_trial(trial_id)
    seen_budgets = {}
    stations = []
    for entry in accepted:
        artifact = entry.artifact
        station = artifact.source.station
        if station in seen_budgets and seen_budgets[station] != artifact.clock_bounds:
            raise ValueError('same station clock budgets must agree')
        seen_budgets[station] = artifact.clock_bounds
        stations.append(StationClockInput(station, artifact.clock_bounds, artifact.evidence))
    return trial, trial.domain.require_time(time), stations, tuple(accepted)


def forecast_clock(
    registry: TrialRegistry, catalog: ClockCatalog, reports: ReportLedger,
    artifact_ids: Iterable[str], time: RationalInput,
) -> PublishedReport:
    trial, forecast_time, stations, accepted = _resolve(registry, catalog, artifact_ids, time)
    phases = project_clock_phases(trial, stations, forecast_time)
    selection = ForecastSelection(trial, forecast_time, accepted)
    return publish_report(reports, selection, phases)
