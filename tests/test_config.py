import unittest
from core.config import registry, chord

class ConfigTests(unittest.TestCase):
    def test_alias(self):
        r = registry({'inventory':{'key':'ctrl+b','aliases':['open bags']}})
        self.assertEqual(r['inventory'],r['open bags'])
        self.assertEqual(r['inventory'],[('key',['ctrl','b'],50)])
    def test_sequence(self):
        r = registry({'example':{'sequence':[{'key':'a'},{'wait':250},{'key':'b','hold':100}]}})
        self.assertEqual(r['example'],[('key',['a'],50),('wait',250),('key',['b'],100)])
    def test_invalid_key(self):
        with self.assertRaises(ValueError):
            chord('ctrl+foobar')
    def test_duplicate(self):
        with self.assertRaises(ValueError):
            registry({'a':{'key':'a','aliases':['b']},'b':{'key':'b'}})
