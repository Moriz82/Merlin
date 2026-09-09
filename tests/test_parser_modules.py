"""Focused coverage for parser dispatch and importer documentation."""
import importlib
import json
from pathlib import Path

import pytest

from workspace.parsers import FORMATS, TRACKS, parse


ROOT = Path(__file__).parents[1]
INPUTS = {
    'nmap_xml': b'<nmaprun><host><address addr="192.0.2.10" addrtype="ipv4"/><ports><port protocol="tcp" portid="443"><state state="open"/></port></ports></host><runstats><finished exit="success"/></runstats></nmaprun>',
    'nmap_text': b'Nmap scan report for 192.0.2.10\n443/tcp open https\n',
    'nmap_gnmap': b'Host: 192.0.2.10 () Ports: 443/open/tcp//https///\n',
    'har': json.dumps({'log': {'entries': [{'request': {'url': 'https://example.test/app'}, 'response': {'status': 200}}]}}).encode(),
    'zap_json': json.dumps({'site': [{'@name': 'https://example.test', 'alerts': []}]}).encode(),
    'burp_xml': b'<items><item><url>https://example.test/app</url></item></items>',
    'linpeas_text': b'linPEAS synthetic\n[+] Operating system\n',
    'winpeas_text': b'winPEAS synthetic\n[+] Operating system\n',
    'bloodhound': json.dumps({'meta': {'version': 5, 'type': 'groups', 'count': 1}, 'data': [{'ObjectIdentifier': 'S-1-5-21-1', 'Properties': {'name': 'GROUP'}}]}).encode(),
    'manual_json': json.dumps({'schema_version': 1, 'assets': [{'id': 'host', 'label': 'Host', 'track': 'linux'}], 'observations': [], 'relationships': []}).encode(),
}


def test_public_dispatch_contract_and_all_dedicated_importers():
    assert set(INPUTS) == set(FORMATS)
    assert TRACKS == ('network', 'web', 'linux', 'windows_ad', 'database_service')
    for format, raw in INPUTS.items():
        result = parse(raw, format)
        assert result == parse(raw, format)
        assert result['assets']


@pytest.mark.parametrize('module', ('common', 'nmap_importer', 'web_importer', 'peas_importer', 'bloodhound_importer', 'manual_importer'))
def test_each_parser_module_has_documentation(module):
    package = ROOT / 'workspace' / 'parsers' / module
    assert (package / '__init__.py').is_file()
    readme = (package / 'README.md').read_text()
    assert len(readme) >= 120
    assert any(word in readme.lower() for word in ('support', 'limit', 'offline', 'partial'))
    assert importlib.import_module(f'workspace.parsers.{module}')


def test_dispatcher_does_not_expose_collection_execution():
    source = (ROOT / 'workspace' / 'parsers' / '__init__.py').read_text()
    assert 'subprocess' not in source and 'socket' not in source
