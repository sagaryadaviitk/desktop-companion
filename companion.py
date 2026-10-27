#!/usr/bin/env python3
"""Compatibility entrypoint for Desktop Companion companion."""
import runpy

if __name__ == '__main__':
    runpy.run_module('desktop_companion.companion', run_name='__main__')
