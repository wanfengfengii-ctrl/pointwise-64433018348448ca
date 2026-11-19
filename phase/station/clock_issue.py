"""Issuance of V2 station clock pieces."""
from collections.abc import Iterable
from hashlib import sha256
from dataclasses import replace
import sqlite3

from phase.observations.ledger import ObservationLedger
from phase.observations.revisions import RevisionRef
from phase.station.clock import ClockArtifact, ClockBounds, make_clock_bounds
from phase.station.clock_artifact import (
    decode_clock_artifact, encode_clock_artifact, same_clock_content,
)
from phase.station.clock_evidence import ClockClause, ClockEvidence
from phase.station.evidence import MAX_OBSERVATIONS
from phase.station.selection import resolve_selection
from phase.storage import initialize, write_transaction
from phase.trials.identities import TrialId, require_id
from phase.trials.registry import TrialRegistry
from phase.trials.stations import StationSource


_SCHEMA = '''
CREATE TABLE IF NOT EXISTS station_clock_issues (
    artifact_id TEXT PRIMARY KEY,
    package TEXT NOT NULL
);
'''


def build_clock_evidence(selection) -> ClockEvidence:
    return ClockEvidence(tuple(
        ClockClause(revision.time, revision.arc) for revision in selection.revisions
    ))


def _draft(
    trial: TrialId, source: StationSource, revisions: tuple[RevisionRef, ...],
    bounds: ClockBounds, evidence: ClockEvidence,
) -> ClockArtifact:
    return ClockArtifact('pending', trial, source, revisions, bounds, evidence)


class ClockIssueLedger:
    def __init__(self, connection: sqlite3.Connection):
        self._db = connection
        initialize(connection, _SCHEMA)

    def issue(
        self, trial: TrialId, source: StationSource, revisions: tuple[RevisionRef, ...],
        bounds: ClockBounds, evidence: ClockEvidence,
    ) -> ClockArtifact:
        if any(ref.key.trial != trial or ref.key.station != source.station for ref in revisions):
            raise ValueError('clock piece revision scope mismatch')
        draft = _draft(trial, source, revisions, bounds, evidence)
        digest = sha256(encode_clock_artifact(draft).encode('utf-8')).hexdigest()
        artifact = replace(draft, artifact_id=f'CK-{digest}')
        package = encode_clock_artifact(artifact)
        decode_clock_artifact(package)  # validate locally issued payloads like imports
        with write_transaction(self._db):
            existing = self.read(artifact.artifact_id)
            if existing is None:
                self._db.execute(
                    'INSERT INTO station_clock_issues VALUES (?, ?)',
                    (artifact.artifact_id, package),
                )
            elif not same_clock_content(existing, artifact):
                raise ValueError('clock piece number conflicts with an existing issue')
        return artifact

    def read(self, artifact_id: str) -> ClockArtifact | None:
        require_id(artifact_id, 'artifact')
        row = self._db.execute(
            'SELECT package FROM station_clock_issues WHERE artifact_id = ?', (artifact_id,)
        ).fetchone()
        return None if row is None else decode_clock_artifact(row[0])


def issue_clock(
    registry: TrialRegistry, observations: ObservationLedger, issues: ClockIssueLedger,
    trial_id: str, station: str, references: Iterable[RevisionRef], clock_bounds,
) -> ClockArtifact:
    refs = tuple(references)
    if not 1 <= len(refs) <= MAX_OBSERVATIONS:
        raise ValueError('select between one and six revisions')
    selection = resolve_selection(registry, observations, trial_id, station, refs)
    bounds = make_clock_bounds(clock_bounds)
    evidence = build_clock_evidence(selection)
    return issues.issue(
        selection.trial.trial_id, selection.source,
        tuple(revision.ref for revision in selection.revisions), bounds, evidence,
    )
