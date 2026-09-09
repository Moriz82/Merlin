"""BloodHound v5 JSON and archive importer."""
import json
from ..common import archive, secret_bearing


def parse(raw, format, context):
    sources = archive(raw) if raw.startswith(b'PK') else [('input.json', raw)]
    for filename, body in sources:
        context.result['quarantined'] |= secret_bearing(body)
        document = json.loads(body)
        meta = document.get('meta', {})
        if meta.get('version') != 5 or meta.get('type') not in ('users', 'groups', 'computers', 'domains', 'ous', 'gpos', 'containers') or not isinstance(document.get('data'), list):
            raise ValueError('Only reviewed BloodHound v5 object arrays are admitted')
        if meta.get('count') != len(document['data']):
            context.result['limitations'].append(f'{filename}: record count differs from metadata.')
        for i, item in enumerate(document['data']):
            id = item.get('ObjectIdentifier')
            if not isinstance(id, str):
                raise ValueError('BloodHound object has no stable identifier')
            props = item.get('Properties', {})
            context.asset(id, props.get('name', id), meta['type'], 'windows_ad')
            context.observed(id, 'Imported directory object', f'{filename}:data[{i}]', collection_methods=meta.get('methods'))
            for member in item.get('Members', []):
                mid = member.get('ObjectIdentifier')
                if isinstance(mid, str):
                    context.asset(mid, mid, member.get('ObjectType', 'directory_object'), 'windows_ad')
                    context.relation(mid, id, 'Imported membership')
        context.result['limitations'].append('Membership and object inventory only. Other directory relationships remain in the source for manual review.')
