"""Setup-only API access using the installed App; credentials never printed."""
import base64
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen


class GitHub:
    def __init__(self):
        root = Path.home() / '.agent-factory/credentials'
        meta = json.loads((root / 'github-app.json').read_text())
        def encode(value):
            return base64.urlsafe_b64encode(value).rstrip(b'=')
        now = int(time.time())
        message = b'.'.join(encode(json.dumps(value).encode()) for value in (
            {'alg': 'RS256', 'typ': 'JWT'},
            {'iat': now - 60, 'exp': now + 540, 'iss': str(meta['app_id'])},
        ))
        signature = subprocess.run(
            ['openssl', 'dgst', '-sha256', '-sign', meta['private_key_path']],
            input=message, capture_output=True, check=True,
        ).stdout
        self.token = (message + b'.' + encode(signature)).decode()
        request = Request(
            f"https://api.github.com/app/installations/{meta['installation_id']}/access_tokens",
            data=b'{}', headers={'Authorization': 'Bearer ' + self.token,
                                'Accept': 'application/vnd.github+json',
                                'User-Agent': 'agent-factory-setup'},
        )
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
        self.token = result['token']

    def api(self, method, path, body=None):
        env = os.environ.copy()
        env['GH_TOKEN'] = self.token
        cmd = ['gh', 'api', '--method', method, path,
               '-H', 'X-GitHub-Api-Version: 2026-03-10']
        if body is not None:
            cmd += ['--input', '-']
        result = subprocess.run(cmd, input=json.dumps(body).encode() if body is not None else None,
                                capture_output=True, env=env)
        if result.returncode:
            raise RuntimeError(result.stderr.decode() + result.stdout.decode())
        return json.loads(result.stdout) if result.stdout else None

    def graphql(self, query, variables=None):
        result = self.api('POST', '/graphql', {'query': query, 'variables': variables or {}})
        if result.get('errors'):
            raise RuntimeError(json.dumps(result['errors']))
        return result['data']
