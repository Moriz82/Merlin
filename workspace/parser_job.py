"""Fixed entry point for one contained parser job."""
import sys
from pathlib import Path

from .parser_service import child


def main():
    if len(sys.argv) != 4:
        return 64
    child(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
