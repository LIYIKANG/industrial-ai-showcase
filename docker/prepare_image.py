"""Build-time only: move initial data out of the code tree and wire persistent paths."""
import json
from pathlib import Path
import shutil

base = Path('/app')
seeds = Path('/opt/showcase-seeds')
for relative, name in json.loads((base / 'docker/storage.json').read_text()):
    source, seed = base / relative, seeds / name
    seed.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.move(str(source), str(seed))
    else:
        seed.mkdir(parents=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.symlink_to(Path('/data') / name, target_is_directory=True)

# Preserve a transactionally consistent copy of original solver project history.
snapshot = base / 'docker/seeds/solver_projects.db'
if snapshot.exists():
    shutil.copy2(snapshot, seeds / 'solver/data/solver_projects.db')
# Start message demonstrations with the shipped baseline, not UI-test mutations.
baseline = seeds / 'bot/mock_db/product_price_database.seed.json'
if baseline.exists():
    shutil.copy2(baseline, baseline.with_name('product_price_database.json'))
