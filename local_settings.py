#!/usr/bin/env python3
"""Compatibility entrypoint for Desktop Companion local_settings."""
import runpy

if __name__ == '__main__':
    runpy.run_module('desktop_companion.local_settings', run_name='__main__')
