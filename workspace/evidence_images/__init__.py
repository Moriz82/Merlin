"""Identify a small, passive image subset for authenticated evidence preview."""
from pathlib import Path


MAX_RENDER_BYTES = 16 * 1024 * 1024
MAX_DIMENSION = 10_000
MAX_PIXELS = 40_000_000
MAX_JPEG_HEADER = 1024 * 1024


def _dimensions(width, height):
    if not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION or width * height > MAX_PIXELS:
        raise ValueError('Image dimensions exceed the preview limit')


def _jpeg_dimensions(raw):
    if not raw.startswith(b'\xff\xd8'):
        raise ValueError('Invalid JPEG header')
    position = 2
    frame_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while position + 4 <= len(raw):
        while position < len(raw) and raw[position] == 0xFF:
            position += 1
        if position >= len(raw):
            break
        marker = raw[position]
        position += 1
        if marker in (0x01, 0xD8, 0xD9):
            continue
        if position + 2 > len(raw):
            break
        length = int.from_bytes(raw[position:position + 2], 'big')
        if length < 2 or position + length > len(raw):
            break
        if marker in frame_markers:
            if length < 7:
                break
            height = int.from_bytes(raw[position + 3:position + 5], 'big')
            width = int.from_bytes(raw[position + 5:position + 7], 'big')
            _dimensions(width, height)
            return width, height
        position += length
    raise ValueError('JPEG dimensions were not found in the bounded header')


def identify(path):
    path = Path(path)
    size = path.stat().st_size
    if not 1 <= size <= MAX_RENDER_BYTES:
        raise ValueError('Image size exceeds the preview limit')
    with path.open('rb') as source:
        raw = source.read(min(size, MAX_JPEG_HEADER))
    if raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) >= 24 and raw[12:16] == b'IHDR':
        _dimensions(int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big'))
        return 'image/png', 'png'
    if raw.startswith((b'GIF87a', b'GIF89a')) and len(raw) >= 10:
        _dimensions(int.from_bytes(raw[6:8], 'little'), int.from_bytes(raw[8:10], 'little'))
        return 'image/gif', 'gif'
    if raw.startswith(b'\xff\xd8'):
        _jpeg_dimensions(raw)
        return 'image/jpeg', 'jpg'
    raise ValueError('Only bounded PNG, GIF, and JPEG evidence can render inline')
