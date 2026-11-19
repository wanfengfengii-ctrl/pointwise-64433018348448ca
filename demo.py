from contextlib import ExitStack, closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from phase.analysis.clock_catalog import ClockCatalog
from phase.analysis.clock_forecast import forecast_clock
from phase.analysis.clock_intake import accept_clock
from phase.analysis.catalog import AcceptedCatalog
from phase.analysis.forecast import forecast
from phase.analysis.intake import accept
from phase.analysis.phase_set import encode_phase_set
from phase.observations.intake import record_arc
from phase.observations.ledger import ObservationLedger
from phase.reports.export import read
from phase.reports.ledger import ReportLedger
from phase.station.artifact import encode_artifact
from phase.station.clock_artifact import encode_clock_artifact
from phase.station.clock_issue import ClockIssueLedger, issue_clock
from phase.station.issue import issue
from phase.station.ledger import StationLedger
from phase.storage import connect
from phase.trials.registry import TrialRegistry, register
from phase.trials.stations import StationSource


SOURCES = {'east': 'east-desk', 'west': 'west-desk'}


def run_case(directory: Path, case: dict) -> None:
    directory.mkdir()
    data = case['input']
    center_path = directory / 'center.sqlite'
    with closing(connect(center_path)) as center:
        registry = TrialRegistry(center)
        catalog = AcceptedCatalog(center)
        reports = ReportLedger(center)
        trial = register(registry, case['case_id'], 'gauge-17', SOURCES, (0, 4), data['drift'])
        selected = []
        with ExitStack() as station_connections:
            for station, source in SOURCES.items():
                local = station_connections.enter_context(closing(connect(directory / f'{station}.sqlite')))
                observations = ObservationLedger(local)
                issues = StationLedger(local)
                revision = record_arc(registry, observations, trial, station, 'flash', *data[station])
                package = issue(registry, observations, issues, trial, station, [revision.ref])
                selected.append(accept(registry, catalog, encode_artifact(package), StationSource(station, source)))
        report = forecast(registry, catalog, reports, selected, data['forecast_at'])
        actual = encode_phase_set(report.phases)
        if actual != case['expected']:
            raise AssertionError(f'PHASE_FORECAST_MISMATCH {case["case_id"]} expected {case["expected"]}, got {actual}')

    with closing(connect(center_path)) as reopened:
        stored = read(ReportLedger(reopened), report.report_id)
        if stored != report:
            raise AssertionError('reopened report differs from the issued snapshot')
        expected_downstream = {
            'phases': case['expected'], 'artifacts': sorted(selected), 'receipt': 'R-000001',
        }
        actual_downstream = {
            'phases': encode_phase_set(stored.phases),
            'artifacts': [entry.artifact_id for entry in stored.provenance],
            'receipt': stored.receipt.receipt_id,
        }
        if actual_downstream != expected_downstream:
            raise AssertionError(f'reopened report expected {expected_downstream}, got {actual_downstream}')

    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': case['case_id'], 'input': data, 'expected': case['expected'], 'actual': actual,
        'downstream_expected': expected_downstream, 'downstream_actual': actual_downstream,
        'code_paths': ['phase/station/issue.py', 'phase/station/calibration.py',
                       'phase/analysis/intake.py', 'phase/analysis/constraints.py',
                       'phase/analysis/forecast.py', 'phase/reports/ledger.py'],
        'oracle': case['oracle'],
    }, ensure_ascii=False, sort_keys=True))


def run_clock_demo(directory: Path) -> None:
    """Issue a V2 clock piece, close the station database, then forecast offline
    and reopen the center database to retrieve the fixed report by its number."""
    directory.mkdir()
    center_path = directory / 'center.sqlite'
    with closing(connect(center_path)) as center:
        registry = TrialRegistry(center)
        catalog = ClockCatalog(center)
        reports = ReportLedger(center)
        # Fixed drift 1/4; station A may offset its clock by exactly one second.
        trial = register(registry, 'clock-work-1', 'gauge-17', SOURCES, (0, 4), ('1/4', '1/4'))
        with ExitStack() as station_connections:
            local = station_connections.enter_context(
                closing(connect(directory / 'clock-east.sqlite')))
            observations = ObservationLedger(local)
            clock_issues = ClockIssueLedger(local)
            # Nominal t=1 reads the 3/8 tick; the fixed +1 s offset lifts it one
            # whole turn, forcing the initial phase to 7/8 of a turn.
            revision = record_arc(registry, observations, trial, 'east',
                                  'flash', 1, '3/8', '3/8')
            piece = issue_clock(registry, observations, clock_issues, trial,
                                'east', [revision.ref], (1, 1))
            selected = [accept_clock(
                registry, catalog, encode_clock_artifact(piece),
                StationSource('east', SOURCES['east']))]
        # The station database is now closed; the center holds everything required.
        report = forecast_clock(registry, catalog, reports, selected, 0)
        actual = encode_phase_set(report.phases)
        expected = [['7/8', '7/8', True, True]]
        if actual != expected:
            raise AssertionError(
                f'CLOCK_FORECAST_MISMATCH expected {expected}, got {actual}')

    with closing(connect(center_path)) as reopened:
        stored = read(ReportLedger(reopened), report.report_id)
        if stored != report:
            raise AssertionError('reopened clock report differs from the issued snapshot')
        downstream = {
            'phases': encode_phase_set(stored.phases),
            'artifacts': [entry.artifact_id for entry in stored.provenance],
            'receipt': stored.receipt.receipt_id,
        }

    print('POINTWISE_CLOCK ' + json.dumps({
        'case_id': 'clock_offset', 'expected': expected, 'actual': actual,
        'downstream': downstream,
        'code_paths': ['phase/station/clock_issue.py', 'phase/station/clock_artifact.py',
                       'phase/analysis/clock_intake.py', 'phase/analysis/clock_constraints.py',
                       'phase/analysis/clock_forecast.py', 'phase/reports/ledger.py'],
        'oracle': '名义 1 秒读到 3/8 刻线，固定 +1 秒钟差把实际时刻移到 2 秒，'
                  '在 1/4 圈每秒频偏下初相为 7/8，共同试验时刻 0 的模一相位即 7/8。',
    }, ensure_ascii=False, sort_keys=True))


def main() -> None:
    cases = json.loads(Path(__file__).with_name('example.json').read_text(encoding='utf-8'))
    with TemporaryDirectory(prefix='strobe-demo-') as name:
        root = Path(name)
        for case in cases:
            run_case(root / case['case_id'], case)
        run_clock_demo(root / 'clock_case')


if __name__ == '__main__':
    main()
