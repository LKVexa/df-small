"""Run native validation in disposable copies, preserving historical evidence."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = next((ROOT / 'vm').iterdir())


def run(args, cwd, env=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=600, env=env)
    print(' '.join(map(str, args)))
    print((result.stdout + result.stderr)[-16000:])
    if result.returncode:
        raise SystemExit(result.returncode)


def main():
    with tempfile.TemporaryDirectory(prefix='df-small-native-') as temp:
        copy = Path(temp) / 'repository'
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns('.git', '.build', '_runs', '__pycache__'))
        vm = copy / 'vm' / PAYLOAD.name
        run(['make'], vm)
        run(['make', 'sanitize'], vm)
        common = ['cc', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                  '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-Isrc', '-Ihost']
        source = str(copy / 'tests/test_host_io.c')
        cases = {'host': ['src/brvm.c', 'src/brasm.c']}
        for name, sources in cases.items():
            executable = vm / ('.build/host_' + name)
            run(common + sources + [source, '-o', str(executable), '-lcrypto'], vm)
            run([str(executable)], vm, {**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1'})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
