#!/usr/bin/env python3
"""Compatibility entrypoint for Desktop Companion remote_access."""
import runpy

if __name__ == '__main__':
    runpy.run_module('desktop_companion.remote_access', run_name='__main__')
