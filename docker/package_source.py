"""Export a rebuildable source archive without environments, keys or live session data."""
from pathlib import Path
import tarfile

base = Path(__file__).resolve().parents[1]
output = base / 'Docker交付包'
output.mkdir(exist_ok=True)
archive = output / '项目演示中心-Docker源码.tar.gz'
excluded_parts = {'.git', '.venv', '__pycache__', '.pytest_cache', 'node_modules',
                  'runtime', 'output', 'outputs', 'logs', 'Docker交付包'}

def include(info):
    relative = Path(info.name).relative_to('项目演示中心')
    name = relative.name
    if any(part in excluded_parts for part in relative.parts):
        return None
    if name == '.DS_Store' or name.startswith(('.env', '.showcase-secret')):
        return None
    if name.endswith(('.log', '.pyc', '.tar', '.tar.gz', '.zip')):
        return None
    if ('.db' in name or '.sqlite' in name) and relative.as_posix() != 'docker/seeds/solver_projects.db':
        return None
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    return info

with tarfile.open(archive, 'w:gz', compresslevel=6) as bundle:
    bundle.add(base, arcname='项目演示中心', filter=include)
print(archive)
