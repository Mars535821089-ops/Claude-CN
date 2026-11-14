"""Version-bound Cowork adapter must preserve native compaction dataflow."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import review_cowork_5114925 as reviewer


class Cowork5114925Tests(unittest.TestCase):
    def test_native_getter_export_and_sdk_propagation_are_exact(self):
        owner = ('function c9r(){let e=Gl()?.autoCompactWindow;'
                 'return typeof e=="number"&&e>0?e:void 0}'
                 'Object.defineProperty(exports,"aU",{enumerable:!0,get:function(){return c9r}})')
        worker = '...t.aU()&&{CLAUDE_CODE_AUTO_COMPACT_WINDOW:String(t.aU())}'
        self.assertTrue(reviewer.native_contract(owner, worker))
        with self.assertRaises(ValueError):
            reviewer.native_contract(owner.replace('return c9r', 'return other'),worker)
        with self.assertRaises(ValueError):
            reviewer.native_contract(owner,worker.replace('String(t.aU())','String(100000)'))


if __name__ == '__main__':
    unittest.main()
