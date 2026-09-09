"""Reviewed manual normalized observation importer."""
from ..common import TRACKS


def parse(raw, format, context):
    import json
    document = json.loads(raw)
    if set(document) != {'schema_version', 'assets', 'observations', 'relationships'} or document['schema_version'] != 1:
        raise ValueError('Expected manual observation schema version 1')
    for asset in document['assets']:
        if set(asset) - {'id', 'label', 'kind', 'track', 'data'} or asset.get('track') not in TRACKS:
            raise ValueError('Invalid manual asset')
        context.asset(asset['id'], asset['label'], asset.get('kind', 'host'), asset['track'], **asset.get('data', {}))
    for observation in document['observations']:
        if set(observation) - {'subject', 'summary', 'location', 'facts'} or observation['subject'] not in context.known:
            raise ValueError('Invalid manual observation')
        context.observed(observation['subject'], observation['summary'], observation.get('location', 'operator'), **observation.get('facts', {}))
    for relation in document['relationships']:
        if set(relation) != {'source', 'target', 'label'} or relation['source'] not in context.known or relation['target'] not in context.known:
            raise ValueError('Invalid manual relationship')
        context.relation(**relation)
