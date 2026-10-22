#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
command -v fnpack >/dev/null || { echo '请先安装飞牛官方 fnpack。' >&2; exit 1; }
python3 - <<'PY'
from pathlib import Path
import hashlib
b=Path('app')
files=sorted(p for p in b.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='SHA256SUMS')
(b/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(b).as_posix()+'\n' for p in files))
PY
(cd app && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.py)
python3 - <<'PY'
from pathlib import Path
import shutil
for directory in Path("app").rglob("__pycache__"):
    shutil.rmtree(directory)
PY
fnpack build --directory .
python3 - <<'PY'
from pathlib import Path
import hashlib,re,shutil
version=re.search(r'^version\s*=\s*(\S+)',Path('manifest').read_text(),re.M).group(1)
out=Path('dist')/('fnos-openclaw-compat-'+version+'-x86.fpk');out.parent.mkdir(exist_ok=True)
shutil.copyfile('fnos-openclaw-compat.fpk',out)
out.with_suffix('.fpk.sha256').write_text(hashlib.sha256(out.read_bytes()).hexdigest()+'  '+out.name+'\n')
print(out)
PY
