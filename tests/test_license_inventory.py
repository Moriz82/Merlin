import json
from importlib.metadata import distribution
from pathlib import Path
import re


PROJECT = Path(__file__).resolve().parents[1]
ALLOWED_NPM_LICENSES = {
    'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'CC-BY-4.0', 'ISC', 'MIT', 'MIT-0',
}


def test_every_locked_python_distribution_has_local_license_evidence():
    names = []
    for line in (PROJECT / 'requirements.lock').read_text().splitlines():
        match = re.match(r'^([A-Za-z0-9_.-]+)==', line)
        if match:
            names.append(match.group(1))
    assert names
    for name in names:
        package = distribution(name)
        files = [str(item).lower() for item in package.files or []]
        assert (
            package.metadata.get('License-Expression')
            or package.metadata.get('License')
            or any('license' in item or 'copying' in item for item in files)
        ), f'{name} has no installed license evidence'


def test_every_locked_frontend_package_has_an_admitted_license_and_integrity():
    lock = json.loads((PROJECT / 'frontend' / 'package-lock.json').read_text())
    packages = {name: value for name, value in lock['packages'].items() if name}
    assert packages
    for name, package in packages.items():
        assert package.get('license') in ALLOWED_NPM_LICENSES, f'{name} has an unreviewed license'
        if 'resolved' in package:
            assert package['resolved'].startswith('https://registry.npmjs.org/')
            assert package.get('integrity', '').startswith('sha512-')


def test_documented_design_references_are_design_only():
    inventory = (PROJECT / 'THIRD-PARTY.md').read_text()
    assert 'Hawkeye' in inventory and 'Gossamer' in inventory
    assert inventory.count('Design reference only; no copied code.') == 2


def test_age_binary_has_pinned_revision_and_retained_notice():
    inventory = (PROJECT / 'THIRD-PARTY.md').read_text()
    dockerfile = (PROJECT / 'Dockerfile').read_text()
    notice = (PROJECT / 'licenses' / 'age-BSD-3-Clause.txt').read_text()
    assert 'age and age-keygen' in inventory
    assert 'v1.3.2' in inventory
    assert 'b74dce4cdbe35b5e5f66c06d9612b72f89028758' in inventory
    assert 'COPY licenses/age-BSD-3-Clause.txt /usr/share/doc/age/copyright' in dockerfile
    assert 'Copyright 2019 The age Authors' in notice
    assert 'Redistributions in binary form must reproduce' in notice
    assert 'ARG AGE_REVISION=b74dce4cdbe35b5e5f66c06d9612b72f89028758' in dockerfile
    assert 'ARG AGE_MODULE_SUM=h1:r6RSZLFSMm6rzKepZ7ZAYkKCu14f3/Me8c7uKYh7C8c=' in dockerfile
    assert 'go mod download -json filippo.io/age@${AGE_REVISION}' in dockerfile
    assert '${AGE_MODULE_SUM}' in dockerfile
    assert dockerfile.count('@${AGE_REVISION}') == 3
    assert dockerfile.count('go install filippo.io/age/cmd/') == 2
    assert '@v1.3.2' not in dockerfile and 'GOSUMDB=off' not in dockerfile
