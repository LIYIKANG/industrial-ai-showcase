import json
import os
import sys
import urllib.request

try:
    port = int(os.getenv('SHOWCASE_PORT', '18080'))
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=4) as response:
        healthy = json.load(response)['ok']
    sys.exit(0 if healthy else 1)
except Exception:
    sys.exit(1)
