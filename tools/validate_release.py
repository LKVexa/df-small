"""Portable release verification without building or modifying sibling VM trees."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'adapter'))
from dfabric import manifest


def main():
    checks = {'manifest': manifest.check_manifest_inventory(str(ROOT)),
              'checksums': manifest.check_sums(str(ROOT))}
    for label, result in checks.items():
        if not result['pass']:
            print(json.dumps({label: result}, indent=2))
            return 1
    rows = (ROOT / 'FILES.sha256').read_text(encoding='utf-8').splitlines()
    seen = set()
    for row in rows:
        digest, rel = row.split('  ', 1)
        if rel in seen or len(digest) != 64:
            raise ValueError('invalid auxiliary checksum inventory')
        seen.add(rel)
        path = Path(manifest._safe_file(str(ROOT), rel))
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'auxiliary checksum mismatch: {rel}')
    expected = set(manifest.walk(str(ROOT))) | {'MANIFEST.json', 'SHA256SUMS.txt'}
    if seen != expected:
        raise ValueError('auxiliary checksum coverage differs from static distribution')
    print(f'Integrity: {len(rows)} static files verified; all root inventories agree.')
    payload = next((ROOT / 'vm').iterdir())
    doc = json.loads((ROOT / 'node/PAYLOAD_DIGEST.json').read_text(encoding='utf-8'))
    if not manifest.check_payload(str(payload), doc)['pass']:
        raise ValueError('embedded payload digest mismatch')
    suites = [(ROOT, ['-m', 'unittest', 'discover', '-s', 'tests', '-v'])]
    for cwd, args in suites:
        result = subprocess.run([sys.executable, '-B', *args], cwd=cwd,
                                capture_output=True, text=True, encoding='utf-8',
                                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, timeout=300)
        output = (result.stdout + result.stderr).strip()
        print(f"{cwd.relative_to(ROOT).as_posix()}: {' '.join(args)} -> {result.returncode}")
        print('\n'.join(output.splitlines()[-5:]) if result.returncode == 0 else output[-10000:])
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
