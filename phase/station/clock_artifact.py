"""JSON encoding and decoding for V2 station clock pieces."""
import json

from phase.observations.revisions import ObservationKey, RevisionRef
from phase.station.clock import CLOCK_VERSION, ClockArtifact, decode_clock_bounds, encode_clock_bounds
from phase.station.clock_evidence import decode_clock_evidence, encode_clock_evidence
from phase.trials.identities import StationId, TrialId, require_id
from phase.trials.stations import StationSource


_FIELDS = {
    'version', 'artifact_id', 'trial_id', 'station', 'source',
    'revisions', 'clock_bounds', 'evidence',
}


def encode_clock_artifact(artifact: ClockArtifact) -> str:
    document = {
        'version': CLOCK_VERSION,
        'artifact_id': artifact.artifact_id,
        'trial_id': artifact.trial_id,
        'station': artifact.source.station,
        'source': artifact.source.source,
        'revisions': [[ref.key.observation, ref.number] for ref in artifact.revisions],
        'clock_bounds': encode_clock_bounds(artifact.clock_bounds),
        'evidence': encode_clock_evidence(artifact.evidence),
    }
    return json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _unique_object(pairs: list) -> dict:
    document = dict(pairs)
    if len(document) != len(pairs):
        raise ValueError('duplicate clock piece field')
    return document


def _decode_revision_refs(raw: object, trial: TrialId, station: StationId) -> tuple[RevisionRef, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError('missing fixed revision references')
    refs = []
    for pair in raw:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('invalid fixed revision reference')
        observation, number = pair
        refs.append(RevisionRef(ObservationKey(trial, station, observation), number))
    if len(set(refs)) != len(refs):
        raise ValueError('repeated fixed revision reference')
    return tuple(refs)


def decode_clock_artifact(payload: str) -> ClockArtifact:
    if not isinstance(payload, str):
        raise ValueError('clock piece must be JSON text')
    try:
        doc = json.loads(payload, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as error:
        raise ValueError('invalid clock piece JSON') from error
    if (not isinstance(doc, dict) or set(doc) != _FIELDS
            or type(doc['version']) is not int or doc['version'] != CLOCK_VERSION):
        raise ValueError('expected a V2 station clock piece')
    artifact_id = require_id(doc['artifact_id'], 'artifact')
    trial = TrialId(require_id(doc['trial_id'], 'trial'))
    source = StationSource(StationId(doc['station']), doc['source'])
    clock_bounds = decode_clock_bounds(doc['clock_bounds'])
    evidence = decode_clock_evidence(doc['evidence'])
    revisions = _decode_revision_refs(doc['revisions'], trial, source.station)
    if len(revisions) != len(evidence.clauses):
        raise ValueError('clock evidence and revision references differ in count')
    return ClockArtifact(artifact_id, trial, source, revisions, clock_bounds, evidence)


def same_clock_content(first: ClockArtifact, second: ClockArtifact) -> bool:
    return first == second
