import sys
import tempfile
import json
import struct
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_smoke_51231 as b


class Build5Tests(unittest.TestCase):
    def test_official_executable_metadata_survives_repack(self):
        from upstream_patch_lib import build_asar,read_asar_header,asar_entry,read_asar_entry,align4
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'official.asar';work=root/'work';work.mkdir()
            (work/'keep.js').write_bytes(b'keep');target=root/'candidate.asar';build_asar(work,target)
            h=json.dumps({'files':{'keep.js':{'offset':'0','size':4,'executable':True}}},separators=(',',':')).encode()
            payload=align4(4+len(h));source.write_bytes(struct.pack('<IIII',4,4+payload,payload,len(h))+h+b'\0'*(payload-4-len(h))+b'keep')
            b.preserve_entry_metadata(source,target)
            raw=target.read_bytes();size,_,header=read_asar_header(raw,target)
            self.assertTrue(asar_entry(header,'keep.js')['executable'])
            self.assertEqual(read_asar_entry(raw,size,header,'keep.js'),b'keep')

    def test_incomplete_pack_is_rejected_without_explicit_smoke(self):
        with patch.object(b.c, 'load', return_value={'frontend': {'x': 'Missing'}}):
            with self.assertRaisesRegex(ValueError, 'missing'):
                b.check_coverage(False)

    def test_output_cannot_overlap_official_source(self):
        with patch.object(b.os.path, 'ismount', return_value=True):
            with self.assertRaises(ValueError):
                b.destination(b.c.BASE / 'Claude.app')
            with self.assertRaises(ValueError):
                b.destination(b.c.BASE / 'Claude.app/derived/Claude.app')
            with self.assertRaises(ValueError):
                b.destination(b.c.BASE / 'implementation-test/../Claude.app/new/Claude.app')
            with self.assertRaises(ValueError):
                b.destination(b.c.BASE / 'migration-preview/new/Claude.app')
            with self.assertRaises(ValueError):
                b.destination(Path('/Applications/Claude.app'))

    def test_mount_absence_fails_closed(self):
        with patch.object(b.os.path, 'ismount', return_value=False):
            with self.assertRaises(ValueError):
                b.destination(b.c.BASE / 'smoke-new/Claude.app')

    def test_unknown_source_never_copies(self):
        with patch.object(b, 'destination', return_value=Path('/unused/Claude.app')), patch.object(
                b, 'check_coverage'), patch.object(b.c, 'read_source', side_effect=ValueError('unknown')), patch.object(
                b.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'unknown'):
                b.build(Path('/unknown'), Path('/unused/Claude.app'), partial_smoke=True)
            run.assert_not_called()
