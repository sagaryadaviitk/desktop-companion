#!/usr/bin/env python3
"""Compatibility entrypoint for Desktop Companion local_smoke."""
import runpy

if __name__ == '__main__':
    runpy.run_module('desktop_companion.local_smoke', run_name='__main__')
