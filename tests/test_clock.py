"""V2 station clock piece issuance, intake and source-disconnection forecast."""
import json
from fractions import Fraction
from pathlib import Path
import unittest

from phase.analysis.clock_catalog import ClockCatalog
from phase.analysis.clock_forecast import forecast_clock
from phase.analysis.clock_intake import accept_clock
from phase.analysis.phase_set import encode_phase_set
from phase.observations.intake import correct, record_arc
from phase.observations.ledger import ObservationLedger
from phase.reports.export import export_report, read
from phase.reports.ledger import ReportLedger
from phase.station.clock import make_clock_bounds
from phase.station.clock_artifact import encode_clock_artifact
from phase.station.clock_issue import ClockIssueLedger, issue_clock
from phase.storage import connect
from phase.trials.registry import TrialRegistry, register
from phase.trials.stations import StationSource

from clock_examples import CASES, SOURCES


class ClockLabTest(unittest.TestCase):
    def setUp(self):
        from tempfile import TemporaryDirectory
        self.directory = TemporaryDirectory(prefix='clock-tests-')
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.center = connect(root / 'center.sqlite')
        self.registry = TrialRegistry(self.center)
        self.catalog = ClockCatalog(self.center)
        self.reports = ReportLedger(self.center)
        self.station_dbs = {}
        self.observations = {}
        self.issues = {}
        for station in SOURCES:
            db = connect(root / f'{station}.sqlite')
            self.station_dbs[station] = db
            self.observations[station] = ObservationLedger(db)
            self.issues[station] = ClockIssueLedger(db)
        self.addCleanup(self.close)

    def close(self):
        for db in self.station_dbs.values():
            db.close()
        self.center.close()

    def reopen_center(self):
        self.center.close()
        self.center = connect(Path(self.directory.name) / 'center.sqlite')
        self.registry = TrialRegistry(self.center)
        self.catalog = ClockCatalog(self.center)
        self.reports = ReportLedger(self.center)

    def close_sources(self):
        for db in self.station_dbs.values():
            db.close()
        self.station_dbs.clear()

    def register_case(self, case_id, case):
        return register(self.registry, case_id, 'gauge-17', SOURCES,
                        case['times'], case['drift'])

    def record_case(self, case_id, case):
        trial = self.register_case(case_id, case)
        selections = []
        for station, bounds, rows in case['packages']:
            refs = [record_arc(self.registry, self.observations[station], trial,
                               station, *row).ref for row in rows]
            selections.append((station, bounds, refs))
        return trial, selections

    def issue_and_accept(self, trial, station, bounds, refs):
        piece = issue_clock(self.registry, self.observations[station],
                            self.issues[station], trial, station, refs, bounds)
        return accept_clock(self.registry, self.catalog, encode_clock_artifact(piece),
                            StationSource(station, SOURCES[station]))


class ClockCaseTests(ClockLabTest):
    def test_all_reference_cases(self):
        for case_id, case in CASES.items():
            with self.subTest(case=case_id):
                trial, selections = self.record_case(case_id, case)
                ids = [self.issue_and_accept(trial, station, bounds, refs)
                       for station, bounds, refs in selections]
                report = forecast_clock(self.registry, self.catalog, self.reports,
                                        ids, case['forecast_at'])
                self.assertEqual(encode_phase_set(report.phases), case['expected'])


class ClockIssueTests(ClockLabTest):
    def test_budget_validation(self):
        for bad in [(), [1], [1, 0], (1.5, 2), ['x', 1]]:
            with self.assertRaises(ValueError):
                make_clock_bounds(bad)
        equal = make_clock_bounds((Fraction(1, 2), '1/2'))
        self.assertEqual((equal.low, equal.high), (Fraction(1, 2), Fraction(1, 2)))

    def test_reissue_same_id_keeps_revision_history(self):
        case = CASES['zero_drift']
        trial = self.register_case('history', case)
        station, bounds, _ = case['packages'][0]
        old = record_arc(self.registry, self.observations[station], trial,
                         station, 'flash', 1, 0, 0)
        first = issue_clock(self.registry, self.observations[station],
                            self.issues[station], trial, station, [old.ref], bounds)
        again = issue_clock(self.registry, self.observations[station],
                            self.issues[station], trial, station, [old.ref], bounds)
        self.assertEqual(again.artifact_id, first.artifact_id)
        new = correct(self.registry, self.observations[station], old.ref.key,
                      old.ref, 1, '1/4', '1/4')
        corrected = issue_clock(self.registry, self.observations[station],
                                self.issues[station], trial, station, [new.ref], bounds)
        self.assertNotEqual(corrected.artifact_id, first.artifact_id)
        self.assertEqual(self.observations[station].read_revision(old.ref), old)

    def test_invalid_revision_selection_leaves_no_issue(self):
        case = CASES['zero_drift']
        trial = self.register_case('bad-sel', case)
        station, bounds, _ = case['packages'][0]
        revision = record_arc(self.registry, self.observations[station], trial,
                              station, 'flash', 1, 0, 0)
        with self.assertRaises(ValueError):
            issue_clock(self.registry, self.observations[station],
                        self.issues[station], trial, station, [], bounds)
        with self.assertRaises(ValueError):
            issue_clock(self.registry, self.observations[station],
                        self.issues[station], trial, station,
                        [revision.ref, revision.ref], bounds)
        self.assertEqual(
            self.station_dbs[station].execute(
                'SELECT COUNT(*) FROM station_clock_issues').fetchone(), (0,))


class ClockIntakeTests(ClockLabTest):
    def _piece(self, case_id='idem'):
        case = CASES['lifted_tick']
        trial, selections = self.record_case(case_id, case)
        station, bounds, refs = selections[0]
        piece = issue_clock(self.registry, self.observations[station],
                            self.issues[station], trial, station, refs, bounds)
        return piece, StationSource(station, SOURCES[station])

    def test_resubmit_same_content_returns_same_id(self):
        piece, source = self._piece()
        first = accept_clock(self.registry, self.catalog,
                             encode_clock_artifact(piece), source)
        rewritten = json.dumps(json.loads(encode_clock_artifact(piece)), indent=2)
        self.assertEqual(
            accept_clock(self.registry, self.catalog, rewritten, source), first)
        self.assertEqual(
            self.center.execute('SELECT COUNT(*) FROM accepted_clock_artifacts').fetchone(),
            (1,))

    def test_same_id_different_content_refused(self):
        piece, source = self._piece()
        accepted = accept_clock(self.registry, self.catalog,
                                encode_clock_artifact(piece), source)
        doc = json.loads(encode_clock_artifact(piece))
        doc['clock_bounds'] = ['0', '2']
        with self.assertRaises(ValueError):
            accept_clock(self.registry, self.catalog, json.dumps(doc), source)
        self.assertEqual(self.catalog.load(accepted).artifact, piece)

    def test_malformed_and_wrong_source_rejected(self):
        piece, source = self._piece()
        doc = json.loads(encode_clock_artifact(piece))
        del doc['evidence']
        with self.assertRaises(ValueError):
            accept_clock(self.registry, self.catalog, json.dumps(doc), source)
        wrong = StationSource('B', 'west-desk')
        with self.assertRaises(ValueError):
            accept_clock(self.registry, self.catalog,
                         encode_clock_artifact(piece), wrong)
        self.assertIsNone(self.catalog.load(piece.artifact_id))

    def test_v1_payload_rejected_by_clock_decoder(self):
        from phase.station.artifact import encode_artifact
        from phase.station.issue import issue
        from phase.station.ledger import StationLedger
        case = CASES['zero_drift']
        trial = self.register_case('v1-reject', case)
        station, _, rows = case['packages'][0]
        ledger = StationLedger(self.station_dbs[station])
        revision = record_arc(self.registry, self.observations[station], trial,
                              station, *rows[0])
        v1 = issue(self.registry, self.observations[station], ledger,
                   trial, station, [revision.ref])
        with self.assertRaises(ValueError):
            accept_clock(self.registry, self.catalog, encode_artifact(v1),
                         StationSource(station, SOURCES[station]))

    def test_unsupported_version_rejected(self):
        piece, source = self._piece('version-three')
        doc = json.loads(encode_clock_artifact(piece))
        doc['version'] = 3
        with self.assertRaises(ValueError):
            accept_clock(self.registry, self.catalog, json.dumps(doc), source)
        self.assertIsNone(self.catalog.load(piece.artifact_id))


class ClockForecastTests(ClockLabTest):
    def test_unknown_empty_and_duplicate_selection(self):
        case = CASES['lifted_tick']
        trial, selections = self.record_case('select', case)
        piece_id = self.issue_and_accept(trial, *selections[0])
        for ids in ([], ['CK-absent'], [piece_id, piece_id]):
            with self.subTest(ids=ids):
                with self.assertRaises(ValueError):
                    forecast_clock(self.registry, self.catalog, self.reports, ids, 0)

    def test_empty_set_keeps_provenance(self):
        case = CASES['same_station']
        trial, selections = self.record_case('empty', case)
        ids = [self.issue_and_accept(trial, station, bounds, refs)
               for station, bounds, refs in selections]
        report = forecast_clock(self.registry, self.catalog, self.reports,
                                ids, case['forecast_at'])
        self.assertEqual(report.phases, ())
        self.assertEqual({entry.artifact_id for entry in report.provenance}, set(ids))

    def test_budget_excluding_trial_domain_signs_empty_set(self):
        case = CASES['time_clipped']
        trial = register(self.registry, 'excluded', 'gauge-17', SOURCES,
                         case['times'], case['drift'])
        station = 'A'
        revision = record_arc(self.registry, self.observations[station], trial,
                              station, 'flash', 1, 0, 0)
        # Every admissible actual time would fall outside the [1,2] domain.
        piece_id = self.issue_and_accept(trial, station, (5, 6), [revision.ref])
        report = forecast_clock(self.registry, self.catalog, self.reports,
                                [piece_id], 1)
        self.assertEqual(report.phases, ())
        self.assertEqual(len(report.provenance), 1)

    def test_isolated_point_phase_preserved(self):
        case = CASES['lifted_tick']
        trial, selections = self.record_case('isolated', case)
        ids = [self.issue_and_accept(trial, station, bounds, refs)
               for station, bounds, refs in selections]
        report = forecast_clock(self.registry, self.catalog, self.reports,
                                ids, case['forecast_at'])
        self.assertEqual(encode_phase_set(report.phases),
                         [['7/8', '7/8', True, True]])

    def test_open_seam_endpoint_preserved(self):
        case = CASES['open_seam']
        trial, selections = self.record_case('seam', case)
        ids = [self.issue_and_accept(trial, station, bounds, refs)
               for station, bounds, refs in selections]
        report = forecast_clock(self.registry, self.catalog, self.reports,
                                ids, case['forecast_at'])
        self.assertEqual(encode_phase_set(report.phases), case['expected'])
        upper = report.phases[-1]
        self.assertTrue(upper.lower_closed)
        self.assertFalse(upper.upper_closed)

    def test_offline_forecast_survives_center_reopen(self):
        case = CASES['separate_stations']
        trial, selections = self.record_case('offline', case)
        ids = [self.issue_and_accept(trial, station, bounds, refs)
               for station, bounds, refs in selections]
        self.close_sources()
        report = forecast_clock(self.registry, self.catalog, self.reports,
                                ids, case['forecast_at'])
        self.assertEqual(encode_phase_set(report.phases), case['expected'])
        exported = export_report(self.reports, report.report_id)
        self.reopen_center()
        self.assertEqual(read(self.reports, report.report_id), report)
        self.assertEqual(export_report(self.reports, report.report_id), exported)

    def test_forecast_time_outside_domain_rejected(self):
        case = CASES['time_clipped']
        trial, selections = self.record_case('domain', case)
        ids = [self.issue_and_accept(trial, station, bounds, refs)
               for station, bounds, refs in selections]
        with self.assertRaises(ValueError):
            forecast_clock(self.registry, self.catalog, self.reports, ids, 5)

    def test_commit_failure_is_retryable_without_duplicate_result(self):
        import sqlite3
        case = CASES['zero_drift']
        trial, selections = self.record_case('retry', case)
        ids = [self.issue_and_accept(trial, station, bounds, refs)
               for station, bounds, refs in selections]
        before = self.center.execute('SELECT COUNT(*) FROM reports').fetchone()
        self.center.executescript('''
            CREATE TABLE allowed_commit_guard (key INTEGER PRIMARY KEY);
            CREATE TABLE deferred_commit_guard (
                key INTEGER REFERENCES allowed_commit_guard(key)
                DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER reject_clock_commit AFTER INSERT ON issue_receipts
            BEGIN
                INSERT INTO deferred_commit_guard VALUES (17);
            END;
        ''')
        with self.assertRaises(sqlite3.IntegrityError):
            forecast_clock(self.registry, self.catalog, self.reports, ids, 0)
        self.assertFalse(self.center.in_transaction)
        self.assertEqual(self.center.execute('SELECT COUNT(*) FROM reports').fetchone(), before)
        self.center.execute('DROP TRIGGER reject_clock_commit')
        report = forecast_clock(self.registry, self.catalog, self.reports, ids, 0)
        self.assertEqual(
            self.center.execute('SELECT COUNT(*) FROM reports').fetchone(),
            (before[0] + 1,))
        self.assertEqual(read(self.reports, report.report_id), report)

    def test_six_revision_limit_enforced(self):
        case = CASES['zero_drift']
        trial = self.register_case('six-ref', case)
        station, bounds, _ = case['packages'][0]
        refs = [record_arc(self.registry, self.observations[station], trial,
                          station, f'flash-{index}', 1, 0, 0).ref for index in range(6)]
        piece_id = self.issue_and_accept(trial, station, bounds, refs)
        extra_trial = self.register_case('six-ref-extra', case)
        extra = record_arc(self.registry, self.observations['B'], extra_trial,
                           'B', 'extra', 1, 0, 0)
        extra_id = self.issue_and_accept(extra_trial, 'B', bounds, [extra.ref])
        with self.assertRaises(ValueError):
            forecast_clock(self.registry, self.catalog, self.reports,
                           [piece_id, extra_id], 0)

    def test_same_station_budget_mismatch_rejected(self):
        case = CASES['zero_drift']
        trial = self.register_case('budget-mismatch', case)
        first = record_arc(self.registry, self.observations['A'], trial,
                           'A', 'one', 1, 0, 0)
        second = record_arc(self.registry, self.observations['A'], trial,
                            'A', 'two', 2, 0, 0)
        id_one = self.issue_and_accept(trial, 'A', (0, 1), [first.ref])
        other = issue_clock(self.registry, self.observations['A'],
                            self.issues['A'], trial, 'A', [second.ref], (0, 2))
        id_two = accept_clock(self.registry, self.catalog,
                              encode_clock_artifact(other),
                              StationSource('A', SOURCES['A']))
        with self.assertRaisesRegex(ValueError, 'budgets'):
            forecast_clock(self.registry, self.catalog, self.reports,
                           [id_one, id_two], 0)


if __name__ == '__main__':
    unittest.main()
