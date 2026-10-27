#!/usr/bin/env python3
"""Compatibility entrypoint for Desktop Companion build_app."""
import runpy

if __name__ == '__main__':
    runpy.run_module('desktop_companion.build_app', run_name='__main__')
