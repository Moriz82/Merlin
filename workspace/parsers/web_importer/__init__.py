"""HAR, ZAP JSON, and Burp XML offline importers."""
import base64
import json
from defusedxml import ElementTree as ET
from ..common import secret_bearing


def parse(raw, format, context):
    if format in ('har', 'zap_json'):
        document = json.loads(raw)
        if format == 'har':
            entries = document.get('log', {}).get('entries')
            if not isinstance(entries, list):
                raise ValueError('Expected HAR log.entries')
            for i, entry in enumerate(entries):
                route = context.web(entry['request']['url'], entry.get('response', {}).get('status'))
                context.observed(route, 'Imported HTTP exchange', f'log.entries[{i}]', method=entry['request'].get('method', 'unknown'))
        else:
            sites = document.get('site')
            if not isinstance(sites, list):
                raise ValueError('Expected ZAP report site array')
            for si, site in enumerate(sites):
                root = context.web(site['@name'])
                for ai, alert in enumerate(site.get('alerts', [])):
                    context.observed(root, alert.get('name', alert.get('alert', 'ZAP candidate')), f'site[{si}].alerts[{ai}]', evidence_state='candidate', cwe=str(alert.get('cweid', '')))
                    for instance in alert.get('instances', []):
                        context.web(instance['uri'])
        return
    root = ET.fromstring(raw)
    if root.tag not in ('items', 'issues'):
        raise ValueError('Expected a Burp items or issues export')
    for i, item in enumerate(root):
        url = item.findtext('url')
        if not url:
            host, path = item.findtext('host'), item.findtext('path')
            url = (host or '') + (path or '')
        route = context.web(url)
        context.observed(route, item.findtext('name') or 'Imported Burp exchange', f'{root.tag}[{i}]', evidence_state='candidate')
        # Request and response payloads remain quarantined; they are never
        # unpacked into normalized views.
        for tag in ('request', 'response'):
            element = item.find(tag)
            if element is not None and element.get('base64') == 'true' and element.text:
                context.result['quarantined'] |= secret_bearing(base64.b64decode(element.text, validate=True))
