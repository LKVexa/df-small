"""Integrity regressions: hostile evidence must not authorize external reads."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapter'))
from dfabric import manifest as m


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        (self.root / 'data.txt').write_bytes(b'content')
        self.digest = hashlib.sha256(b'content').hexdigest()

    def seal(self):
        files = m.inventory(str(self.root))
        self.doc = {'files': files, 'file_count': len(files)}
        (self.root / 'MANIFEST.json').write_text(json.dumps(self.doc), encoding='utf-8')
        m.write_sums(str(self.root))

    def test_valid_inventory_and_checksums(self):
        self.seal()
        self.assertTrue(m.check_sums(str(self.root))['pass'])
        self.assertTrue(m.check_manifest_inventory(str(self.root))['pass'])

    def test_traversal_absolute_windows_and_alias_paths_never_opened(self):
        for path in ('../outside', '/outside', 'C:/outside', 'data.txt:stream', './data.txt',
                     'x/../data.txt', 'x//data.txt', 'data.txt.', 'data.txt ', 'x\\outside'):
            with self.subTest(path=path):
                (self.root / 'SHA256SUMS.txt').write_text(f'{self.digest}  {path}\n', encoding='utf-8')
                with patch.object(m, 'sha256_file', side_effect=AssertionError('must not hash invalid path')):
                    self.assertFalse(m.check_sums(str(self.root))['pass'])
                doc = {'file_count': 1, 'files': [{'path': path, 'bytes': 7, 'sha256': self.digest}]}
                (self.root / 'MANIFEST.json').write_text(json.dumps(doc), encoding='utf-8')
                with patch.object(m, 'sha256_file', side_effect=AssertionError('must not hash invalid path')):
                    self.assertFalse(m.check_manifest_inventory(str(self.root))['pass'])

    def test_empty_and_malformed_checksum_evidence_fails(self):
        for text in ('', 'junk\n', 'z' * 64 + '  data.txt\n'):
            (self.root / 'SHA256SUMS.txt').write_text(text, encoding='utf-8')
            self.assertFalse(m.check_sums(str(self.root))['pass'])

    def test_duplicate_checksum_is_rejected(self):
        self.seal()
        with (self.root / 'SHA256SUMS.txt').open('a', encoding='utf-8') as f:
            f.write(f'{self.digest}  data.txt\n')
        self.assertFalse(m.check_sums(str(self.root))['pass'])

    def test_duplicate_inventory_record_and_member_are_rejected(self):
        self.seal()
        self.doc['files'] *= 2
        self.doc['file_count'] = 2
        (self.root / 'MANIFEST.json').write_text(json.dumps(self.doc), encoding='utf-8')
        self.assertFalse(m.check_manifest_inventory(str(self.root))['pass'])
        (self.root / 'MANIFEST.json').write_text('{"files":[],"files":[],"file_count":0}', encoding='utf-8')
        self.assertFalse(m.check_manifest_inventory(str(self.root))['pass'])

    def test_boolean_size_and_count_are_rejected(self):
        self.seal()
        for key in ('bytes', 'file_count'):
            doc = json.loads(json.dumps(self.doc))
            (doc['files'][0] if key == 'bytes' else doc)[key] = True
            (self.root / 'MANIFEST.json').write_text(json.dumps(doc), encoding='utf-8')
            self.assertFalse(m.check_manifest_inventory(str(self.root))['pass'])

    def test_tampering_and_unlisted_files_fail(self):
        self.seal()
        (self.root / 'data.txt').write_bytes(b'tamper!')
        self.assertFalse(m.check_sums(str(self.root))['pass'])
        self.assertFalse(m.check_manifest_inventory(str(self.root))['pass'])
        self.seal()
        (self.root / 'extra').write_bytes(b'extra')
        self.assertFalse(m.check_sums(str(self.root))['pass'])
        self.assertFalse(m.check_manifest_inventory(str(self.root))['pass'])

    def test_git_metadata_and_auxiliary_digest_are_not_recursive(self):
        self.seal()
        (self.root / '.git').mkdir()
        (self.root / '.git/config').write_text('local config', encoding='utf-8')
        (self.root / 'FILES.sha256').write_text('auxiliary manifest', encoding='utf-8')
        self.assertTrue(m.check_sums(str(self.root))['pass'])
        self.assertTrue(m.check_manifest_inventory(str(self.root))['pass'])

    def test_linked_file_and_directory_refused(self):
        outside = self.root.parent / 'outside'
        outside.mkdir()
        (outside / 'secret').write_bytes(b'secret')
        link = self.root / 'link'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('creating filesystem links requires OS privilege')
        with self.assertRaises(ValueError):
            m.inventory(str(self.root))
        link.unlink()
        link.symlink_to(outside / 'secret')
        with self.assertRaises(ValueError):
            m.inventory(str(self.root))

    def test_payload_byte_count_tampering_is_rejected(self):
        doc = m.payload_digest(str(self.root))
        self.assertTrue(m.check_payload(str(self.root), doc)['pass'])
        doc['files'][0]['bytes'] += 1
        self.assertFalse(m.check_payload(str(self.root), doc)['pass'])


if __name__ == '__main__':
    unittest.main()
