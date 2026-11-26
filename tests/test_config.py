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
    def test_text_sequence(self):
        r = registry({'thanks':{'sequence':[{'key':'enter'},{'text':'/thank ä'},{'key':'enter'}]}})
        self.assertEqual(r['thanks'],[('key',['enter'],50),('text','/thank ä'),('key',['enter'],50)])
    def test_invalid_text(self):
        for step in ({'text':''},{'text':'a\nb'},{'text':'a','key':'b'}):
            with self.assertRaises(ValueError):
                registry({'example':{'sequence':[step]}})

    def test_output_enabled(self):
        from core.config import output_enabled
        self.assertFalse(output_enabled({'output':{}}))
        self.assertTrue(output_enabled({'output':{'enabled':True}}))
        self.assertFalse(output_enabled({'output':{'enabled':True}},False))
        self.assertTrue(output_enabled({'output':{'enabled':False}},True))
        with self.assertRaises(ValueError):
            output_enabled({'output':{'enabled':'false'}})
