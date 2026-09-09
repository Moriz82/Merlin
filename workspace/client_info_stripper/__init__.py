"""Local field minimization. Output remains client-confidential."""
import hashlib
import hmac
from ..store import digest


def minimize(records, key):
    if len(key) < 32:
        raise ValueError('Use a private engagement key with at least 32 bytes')
    def alias(value):
        return 'asset-' + hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()[:20]
    assets, relationships = [], []
    asset_ids = {r['id'] for r in records if r['kind'] == 'asset'}
    for r in records:
        d = r['data']
        if r['kind'] == 'asset':
            facts = d.get('data', {})
            selected = {}
            for field in ('port', 'protocol'):
                value = facts.get(field)
                if field == 'port' and type(value) is int and 1 <= value <= 65535 or field == 'protocol' and value in ('tcp', 'udp', 'sctp'):
                    selected[field] = value
            assets.append({'id': alias(r['id']), 'kind': d['kind'] if d['kind'] in ('host', 'service', 'application', 'route', 'users', 'groups', 'computers', 'domains', 'ous', 'gpos', 'containers') else 'other', 'track': d['track'], 'facts': selected})
        elif r['kind'] == 'relationship' and d['source'] in asset_ids and d['target'] in asset_ids:
            relationships.append({'source': alias(d['source']), 'target': alias(d['target']), 'kind': 'imported_relationship'})
    output = {'schema_version': 1, 'classification': 'client-confidential-minimized', 'assets': assets, 'relationships': relationships}
    receipt = {'input_hash': digest(records), 'output_hash': digest(output), 'human_review_required': True, 'disclosure_authorized': False, 'removed': ['names', 'addresses', 'URLs', 'prose', 'raw evidence', 'credentials', 'source paths']}
    return {'output': output, 'receipt': receipt}
