#!/usr/bin/env python3
"""Compatibility entrypoint for Desktop Companion control."""
import runpy

if __name__ == '__main__':
    runpy.run_module('desktop_companion.control', run_name='__main__')
else:
    # A pre-upgrade server imports this lazily while an in-place update is
    # applied. Keep its status endpoints working until that process restarts.
    from desktop_companion.control import read_snapshot
