from pathlib import Path

import pytest

from workspace.evidence_images import MAX_RENDER_BYTES, identify


def write(path, body):
    path.write_bytes(body)
    return path


def test_identifies_bounded_png_gif_and_jpeg(tmp_path):
    png = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + (1).to_bytes(4, 'big') + (1).to_bytes(4, 'big')
    gif = b'GIF89a' + (2).to_bytes(2, 'little') + (3).to_bytes(2, 'little')
    jpeg = b'\xff\xd8\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xd9'
    assert identify(write(tmp_path / 'image.png', png)) == ('image/png', 'png')
    assert identify(write(tmp_path / 'image.gif', gif)) == ('image/gif', 'gif')
    assert identify(write(tmp_path / 'image.jpg', jpeg)) == ('image/jpeg', 'jpg')


@pytest.mark.parametrize('body', [
    b'<svg xmlns="http://www.w3.org/2000/svg"/>',
    b'GIF89a\x00\x00\x01\x00',
    b'\xff\xd8\xff\xd9',
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + (10_001).to_bytes(4, 'big') + (1).to_bytes(4, 'big'),
])
def test_rejects_active_unknown_incomplete_and_oversized_dimensions(tmp_path, body):
    with pytest.raises(ValueError):
        identify(write(tmp_path / 'blocked.bin', body))


def test_rejects_files_above_inline_limit(tmp_path):
    path = Path(tmp_path / 'large.png')
    with path.open('wb') as target:
        target.seek(MAX_RENDER_BYTES)
        target.write(b'x')
    with pytest.raises(ValueError):
        identify(path)
