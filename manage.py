#!/usr/bin/env python3
"""Compatibility entrypoint for Desktop Companion manage."""
import runpy

if __name__ == '__main__':
    runpy.run_module('desktop_companion.manage', run_name='__main__')
