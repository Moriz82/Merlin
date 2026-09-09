"""Offline parser dispatcher with dedicated, format-scoped importers."""
from .common import MAX_RECORDS, TRACKS, archive, safe_text, secret_bearing, new_result, ParseContext
from . import bloodhound_importer, manual_importer, nmap_importer, peas_importer, web_importer


FORMATS = ('nmap_xml', 'nmap_text', 'nmap_gnmap', 'zap_json', 'har', 'burp_xml', 'linpeas_text', 'winpeas_text', 'bloodhound', 'manual_json')


def _finish(result):
    if sum(len(result[key]) for key in ('assets', 'relationships', 'observations')) > MAX_RECORDS:
        raise ValueError('Normalized record limit exceeded')
    if not result['assets']:
        result['limitations'].append('No supported assets were found. This is not a clean assessment result.')
    result['limitations'] = sorted(set(result['limitations']))
    result['complete'] = not result['limitations']
    if result['quarantined']:
        result['assets'], result['relationships'], result['observations'] = [], [], []
        result['complete'] = False
        result['limitations'] = ['Possible secret-bearing content. The original is quarantined. Produce a reviewed derivative before import.']
    return result


def parse(raw, format):
    if format not in FORMATS or len(raw) > 256 * 1024**2 or raw.startswith(b'SQLite format 3'):
        raise ValueError('Unsupported format or input size')
    result = new_result(raw)
    context = ParseContext(result)
    if format in ('nmap_xml', 'nmap_text', 'nmap_gnmap'):
        nmap_importer.parse(raw, format, context)
    elif format in ('har', 'zap_json', 'burp_xml'):
        web_importer.parse(raw, format, context)
    elif format in ('linpeas_text', 'winpeas_text'):
        peas_importer.parse(raw, format, context)
    elif format == 'bloodhound':
        bloodhound_importer.parse(raw, format, context)
    elif format == 'manual_json':
        manual_importer.parse(raw, format, context)
    return _finish(result)


__all__ = ['FORMATS', 'TRACKS', 'archive', 'parse', 'safe_text', 'secret_bearing']
