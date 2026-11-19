"""Center catalog of accepted V2 station clock pieces."""
from dataclasses import dataclass
import sqlite3

from phase.station.clock_artifact import (
    decode_clock_artifact, encode_clock_artifact, same_clock_content,
)
from phase.storage import initialize, write_transaction
from phase.trials.identities import StationId, require_id
from phase.trials.stations import StationSource


_SCHEMA = '''
CREATE TABLE IF NOT EXISTS accepted_clock_artifacts (
    artifact_id TEXT PRIMARY KEY,
    station_id TEXT NOT NULL,
    source TEXT NOT NULL,
    package TEXT NOT NULL
);
'''


@dataclass(frozen=True)
class AcceptedClock:
    artifact: object
    source: StationSource


class ClockCatalog:
    def __init__(self, connection: sqlite3.Connection):
        self._db = connection
        initialize(connection, _SCHEMA)

    def store(self, artifact, source: StationSource) -> str:
        if artifact.source != source:
            raise ValueError('received source differs from clock piece')
        with write_transaction(self._db):
            existing = self.load(artifact.artifact_id)
            if existing is None:
                self._db.execute(
                    'INSERT INTO accepted_clock_artifacts VALUES (?, ?, ?, ?)',
                    (artifact.artifact_id, source.station, source.source,
                     encode_clock_artifact(artifact)),
                )
            elif (existing.source != source
                  or not same_clock_content(existing.artifact, artifact)):
                raise ValueError('clock piece number is already bound to different content')
        return artifact.artifact_id

    def load(self, artifact_id: str) -> AcceptedClock | None:
        require_id(artifact_id, 'artifact')
        row = self._db.execute(
            'SELECT station_id, source, package FROM accepted_clock_artifacts WHERE artifact_id = ?',
            (artifact_id,),
        ).fetchone()
        if row is None:
            return None
        station, source, package = row
        artifact = decode_clock_artifact(package)
        if artifact.artifact_id != artifact_id:
            raise ValueError('stored clock piece number differs from its catalog key')
        return AcceptedClock(artifact, StationSource(StationId(station), source))
