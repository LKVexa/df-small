import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
VM = next((ROOT / 'vm').iterdir())
sys.path.insert(0, str(ROOT / 'adapter'))
from dfabric.nodes import SmallNodeAdapter
from dfabric import AdapterRefusal
class PortableSecurity(unittest.TestCase):
    def test_output_path_escapes_refused_before_lowering(self):
        adapter = object.__new__(SmallNodeAdapter)
        with patch('dfabric.nodes.W.lowering_record') as lower:
            for unit in ('../escape', '/tmp/escape', 'C:/escape', r'..\escape',
                         'safe/../../escape', '', '.', '..', 'NUL', 'COM1.log',
                         'name.', 'injected\nrow', 'x' * 129, None):
                with self.subTest(unit=unit), self.assertRaises(AdapterRefusal):
                    adapter.submit_words([1], unit_id=unit)
            lower.assert_not_called()


if __name__ == '__main__':
    unittest.main()
