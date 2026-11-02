#!/usr/bin/env python3
"""Run against an explicitly selected disposable loopback Lobu install.

The local-init credential is passed only in the benchmark child environment.
This helper never selects or authenticates a cloud workspace.
"""
import argparse
import json
import os
import subprocess
import urllib.parse
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url', required=True)
parser.add_argument('--config', default='configs/correction-relationships-pilot.json')
args = parser.parse_args()
url = urllib.parse.urlsplit(args.base_url)
if url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1'} or url.username or url.password or url.query or url.fragment or url.path not in {'', '/'}:
    parser.error('--base-url must explicitly identify a disposable HTTP loopback install')
base = args.base_url.rstrip('/')
request = urllib.request.Request(base + '/api/local-init', data=b'', method='POST', headers={'X-Lobu-Client': 'lobu-cli'})
with urllib.request.urlopen(request, timeout=30) as response:
    bootstrap = json.load(response)
token = bootstrap.get('device_token')
org = (bootstrap.get('organization') or {}).get('slug')
if not token or not org:
    raise SystemExit('Local initialization did not return the required benchmark scope')
version = subprocess.run(['lobu', '--version'], capture_output=True, text=True, check=True).stdout.strip()
env = {**os.environ, 'LOBU_BASE_URL': base, 'LOBU_ORG_SLUG': org, 'LOBU_API_TOKEN': token, 'LOBU_VERSION': version}
raise SystemExit(subprocess.run(['bun', 'run', 'scripts/run.ts', '--config', args.config], env=env).returncode)
