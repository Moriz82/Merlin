"""One-job-at-a-time parser service for a Docker container with no network."""
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time
import uuid
from .parsers import FORMATS

STOP = False


def stopping(*_):
    global STOP
    STOP = True


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (110, 110))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024**2, 128 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def atomic_file(path, data):
    """Publish a complete parser result only after its bytes are durable."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4()}.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            try:
                written = os.write(fd, view)
            except InterruptedError:
                continue
            if written <= 0:
                raise OSError('Parser result write stopped')
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary, path)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def fixed_error(path):
    atomic_file(path, b'The parser did not accept this file. No records were merged.\n')


def file_sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def child(input_path, output_path, format):
    """Run inside a bounded child. Paths are generated UUID names in one queue."""
    limits()
    from .parsers import parse
    raw = input_path.read_bytes()
    result = parse(raw, format)
    encoded = json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
    if len(encoded) > 128 * 1024**2:
        raise ValueError('Parser output exceeds the limit')
    atomic_file(output_path, encoded)


def serve(queue):
    root = Path(queue).absolute()
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError('Parser queue is not a directory')
    for running in root.glob('*.running'):
        try:
            fixed_error(running.with_suffix('.error'))
        except FileExistsError:
            pass
        for temporary in root.glob(f'.{running.stem}.*.tmp'):
            temporary.unlink(missing_ok=True)
        running.unlink(missing_ok=True)
    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    while not STOP:
        requests = sorted(root.glob('*.request'), key=lambda p: p.stat().st_mtime_ns)
        if not requests:
            time.sleep(.1)
            continue
        request_path = requests[0]
        job = request_path.stem
        try:
            uuid.UUID(job)
            running = request_path.with_suffix('.running')
            os.replace(request_path, running)
            if running.stat().st_size > 4096:
                raise ValueError('Job request exceeds its limit')
            request = json.loads(running.read_text())
            if set(request) != {'schema_version', 'job_id', 'format', 'sha256', 'size'} or request['schema_version'] != 1 or request['job_id'] != job or request['format'] not in FORMATS or type(request['size']) is not int or not 0 <= request['size'] <= 256 * 1024**2:
                raise ValueError('Invalid job request')
            source = root / f'{job}.input'
            if source.is_symlink() or source.stat().st_size != request['size'] or file_sha256(source) != request['sha256']:
                raise ValueError('Input integrity failed')
            output = root / f'{job}.response'
            application_root = Path(__file__).resolve().parent.parent
            process = subprocess.Popen(
                [sys.executable, '-B', '-m', 'workspace.parser_job', str(source), str(output), request['format']],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(application_root),
                env={'PATH': '/usr/local/bin:/usr/bin', 'PYTHONPATH': str(application_root)},
                close_fds=True,
                start_new_session=True,
            )
            deadline = time.monotonic() + 120
            while process.poll() is None and time.monotonic() < deadline and not (root / f'{job}.cancel').exists() and not STOP:
                time.sleep(.1)
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            code = process.wait(timeout=5)
            if code or not output.exists():
                raise RuntimeError('Child failed')
        except Exception:
            try:
                fixed_error(root / f'{job}.error')
            except FileExistsError:
                pass
        finally:
            try:
                (root / f'{job}.running').unlink()
            except FileNotFoundError:
                pass
            for temporary in root.glob(f'.{job}.*.tmp'):
                temporary.unlink(missing_ok=True)


def main():
    if len(sys.argv) != 2:
        print('usage: python -m workspace.parser_service QUEUE', file=sys.stderr)
        return 64
    try:
        serve(sys.argv[1])
    except Exception as error:
        print(f'Cannot start parser service: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
