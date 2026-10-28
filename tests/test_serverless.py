import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ServerlessFilesystemTests(unittest.TestCase):
    def test_import_and_upload_directory_work_with_read_only_source(self):
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, VERCEL='1', PYTHONDONTWRITEBYTECODE='1',
                       TEMP=temp, TMP=temp, TMPDIR=temp)
            script = '''
from pathlib import Path
import sys
import tempfile
root = Path.cwd().resolve()
def readonly(event, args):
    if event == 'os.mkdir' and root in Path(args[0]).resolve().parents:
        raise PermissionError('Source directory is read-only')
sys.addaudithook(readonly)
from backend.app import UPLOAD_DIR
path = Path(UPLOAD_DIR).resolve()
assert Path(tempfile.gettempdir()).resolve() in path.parents
probe = path / 'upload-probe.tmp'
probe.write_bytes(b'upload')
assert probe.read_bytes() == b'upload'
probe.unlink()
'''
            result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
