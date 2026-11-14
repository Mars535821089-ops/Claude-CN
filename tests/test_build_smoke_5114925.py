"""The current signed source may only become an isolated, non-installable sample."""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_smoke_5114925 as builder


class CurrentClaudeBuildTests(unittest.TestCase):
    def test_app_stage_requires_fresh_external_slot_and_never_current_app(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            stage=root/'implementation-verified/Claude.app'
            with mock.patch.object(builder,'EXTERNAL',root), \
                 mock.patch.object(builder,'external_mounted',return_value=True):
                self.assertEqual(builder.destination(stage),stage)
                stage.parent.mkdir()
                with self.assertRaises(ValueError):
                    builder.destination(stage)
                with self.assertRaises(ValueError):
                    builder.destination(root/'Claude.app')
                with self.assertRaises(ValueError):
                    builder.destination(Path('/Applications/Claude.app'))

    def test_edit_scope_is_unique_source_pinned_and_never_a_noop(self):
        edits = {}
        key = ('ion-dist.asar', 'i18n/zh-CN.json')
        source = b'old'
        builder.register_edit(edits, key, source, b'new',
                              hashlib.sha256(source).hexdigest())
        self.assertEqual(edits[key], b'new')
        with self.assertRaises(ValueError):
            builder.register_edit(edits, key, source, b'again',
                                  hashlib.sha256(source).hexdigest())
        with self.assertRaises(ValueError):
            builder.register_edit({}, key, source, b'new', '0' * 64)
        with self.assertRaises(ValueError):
            builder.register_edit({}, key, source, source,
                                  hashlib.sha256(source).hexdigest())
        with self.assertRaises(ValueError):
            builder.register_edit({}, ('../app.asar', 'evil'), None, b'new', None)


if __name__ == '__main__':
    unittest.main()
