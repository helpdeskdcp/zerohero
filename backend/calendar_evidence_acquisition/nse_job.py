"""Cron/systemd-compatible evidence job; dates come from observed final reports."""
import sys
from .nse_downloader import main as downloader_main


def main(argv=None):
    return downloader_main(['--latest-completed', *(sys.argv[1:] if argv is None else argv)])

if __name__ == '__main__':
    raise SystemExit(main())
