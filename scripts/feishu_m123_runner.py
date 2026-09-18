"""Container entrypoint for isolated bot M1/M2/M3 jobs. No QA/Judge fallback."""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request


def provision(base_url, count=8):
    key = os.environ['ECHOMEM_PROVISIONING_AUTH_KEY']
    def post(path, body, bootstrap=''):
        headers = {'Content-Type': 'application/json', 'X-EchoMem-Provisioning-Key': key}
        if bootstrap:
            headers['X-EchoMem-Bootstrap-Key'] = bootstrap
        req = urllib.request.Request(base_url + path, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    tenants = []
    for i in range(count):
        result = post('/api/auth/tenants', {'name': f'm123-{os.getpid()}-{i}'})
        tenant = result['tenant']['tenant_id']
        bootstrap = result.get('bootstrap_key', '')
        result = post(f'/api/auth/tenants/{tenant}/users', {}, bootstrap)
        user = result['user']['user_id']
        result = post(f'/api/auth/tenants/{tenant}/users/{user}/key', {}, bootstrap)
        auth = result.get('auth_key') or result.get('key', {}).get('auth_key')
        if not auth:
            raise RuntimeError('Tenant provisioning returned no auth key')
        tenants.append({'tenant_id': tenant, 'user_id': user, 'auth_key': auth})
        print(f'Independent tenants ready: {i + 1}/{count}', flush=True)
    return tenants


def main():
    os.umask(0o077)
    private = Path('/private'); private.mkdir(exist_ok=True)
    tenants = provision(os.environ['STRESS_BASE_URL'])
    (private / 'tenants.json').write_text(json.dumps({'tenants': tenants}))
    # Remove provisioning capability from the load subprocess environment.
    os.environ.pop('ECHOMEM_PROVISIONING_AUTH_KEY', None)
    cmd = [sys.executable, '-m', 'performance.targets.echomem.observation_run',
           '--profiles', '/out/stress-profile.json', '--out-dir', '/out', '--metrics', 'M1,M2,M3']
    result = subprocess.run(cmd, check=False)
    if not Path('/out/report.html').is_file():
        raise RuntimeError('WRONG_ENTRYPOINT: missing M1/M2/M3 report.html')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
