"""Initialize an empty volume without overwriting any saved demonstration data."""
import json
import os
from pathlib import Path
import shutil
import sys

base = Path('/app')
for _, name in json.loads((base / 'docker/storage.json').read_text()):
    seed, target = Path('/opt/showcase-seeds') / name, Path('/data') / name
    target.mkdir(parents=True, exist_ok=True)
    for source in seed.rglob('*'):
        dest = target / source.relative_to(seed)
        if source.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        elif not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)

os.chdir(base)
os.execv(sys.executable, [sys.executable, str(base / 'launcher.py'), '--no-browser'])
