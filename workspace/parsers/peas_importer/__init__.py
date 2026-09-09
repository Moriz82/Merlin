"""LinPEAS and WinPEAS text evidence indexer."""
import re


def parse(raw, format, context):
    text = raw.decode('utf8', 'replace')
    marker = 'linpeas' if format == 'linpeas_text' else 'winpeas'
    if marker not in text.lower():
        raise ValueError('Tool signature is not present; retain as manual evidence')
    track = 'linux' if marker == 'linpeas' else 'windows_ad'
    aid = context.asset('unassigned-host', 'Unassigned imported host', 'host', track)
    context.result['limitations'].append('PEAS text is a partial evidence index. Assign the host and review the original output. No privilege or vulnerability conclusion is inferred.')
    clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    for i, line in enumerate(clean.splitlines(), 1):
        if re.match(r'^(?:[╔╚═─┌└]+|\[\+\]|\[\*\])', line.strip()):
            context.observed(aid, line.strip()[:240], f'line:{i}')
