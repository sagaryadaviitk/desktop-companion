#!/usr/bin/env python3
"""Compatibility entrypoint for the approved Desktop Companion login job."""
import importlib.util
import runpy
import sys

try:
    # Resolve and compile the entrypoint before running it, so a missing or
    # quarantined package cannot send launchd into a retry loop. Runtime errors
    # after startup still reach the supervisor and retain their normal status.
    spec = importlib.util.find_spec('desktop_companion.service')
    if spec is None or spec.loader is None:
        raise ImportError('Desktop Companion service is unavailable')
    spec.loader.get_code('desktop_companion.service')
except (ImportError, OSError, SyntaxError):
    if sys.argv[1:] == ['run']:
        print('Companion dependencies are unavailable. Automatic startup stopped; review security status and repair the approved installation.', file=sys.stderr, flush=True)
        raise SystemExit(0)
    raise

if __name__ == '__main__':
    runpy.run_module('desktop_companion.service', run_name='__main__')
