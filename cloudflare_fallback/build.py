#!/usr/bin/env python3
"""Build the standalone Cloudflare Worker without dependencies or credentials."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def source():
    # Escaping spaces preserves exact HTML when a native editor's double-space
    # substitution is enabled. The JavaScript string decodes to the original page.
    page=json.dumps((ROOT/'offline.html').read_text()).replace(' ',r'\u0020')
    return (ROOT/'worker.mjs.template').read_text().replace('__COMPANION_OFFLINE_HTML__',page).replace('  ','\t')

if __name__=='__main__':
    target=ROOT.parent/'.state/exports/companion-fallback.mjs'
    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    target.write_text(source());target.chmod(0o600)
    print('Built: '+str(target))
