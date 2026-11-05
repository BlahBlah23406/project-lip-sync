import importlib.util
import unittest
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

class SacredTermsTest(unittest.TestCase):
    def test_dotenv_discovery(self):
        for source in [Path('translator.py'), Path('../projects/lip-sync/translator.py')]:
            with tempfile.TemporaryDirectory() as directory:
                copy = Path(directory) / 'translator_under_test.py'
                copy.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
                (Path(directory) / '.env').write_text('LLM_API_KEY=synthetic-test-key\n', encoding='utf-8')
                env = dict(os.environ)
                env.pop('LLM_API_KEY', None)
                command = "import importlib.util,os; spec=importlib.util.spec_from_file_location('under_test'," + repr(str(copy)) + "); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);assert os.environ['LLM_API_KEY']=='synthetic-test-key';print('dotenv discovery PASS')"
                result = subprocess.run([sys.executable, '-X', 'utf8', '-c', command], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                print(source, result.stdout.strip())

    def test_both_translators(self):
        for path in [Path('translator.py'), Path('../projects/lip-sync/translator.py')]:
            spec = importlib.util.spec_from_file_location('tested_translator', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for source in ['Prophet ﷺ said', 'Umar (RA), Musa AS', 'Subhanallah, Insha’Allah, dua, fitna, Hidayah', 'Allah (SWT), Allah SWT', 'ﷺ ﷺ', 'Allah ﷻ, Imam RH, Musa عليه السلام', '(saw), (pbuh)']:
                protected, mapping = module.pre_process_honorifics(source)
                self.assertTrue(mapping, source)
                restored = module.post_process_honorifics(protected, mapping)
                self.assertNotIn('__SACRED_', restored)
                print(path, source, '=>', restored)
            text = 'as soon as I saw grass in class'
            self.assertEqual(module.pre_process_honorifics(text), (text, {}))
            protected, mapping = module.pre_process_honorifics('ﷺ ﷺ')
            self.assertEqual(len(mapping), 2)
            with self.assertRaises(ValueError):
                module.post_process_honorifics('omitted', mapping)
            with self.assertRaises(ValueError):
                module.post_process_honorifics(protected + protected, mapping)
            source = [{"text": "Allah and Musa AS", "start": 0.0, "duration": 3.0}]
            def echo_batch(batch):
                return ([{"text": seg["text"], "speaker": "SPEAKER_B", "is_arabic_quote": False} for seg in batch], 7, 8)
            for use_ollama in (False, True):
                with patch.dict(module.os.environ, {"LLM_API_KEY": "fake" if use_ollama else ""}), patch.object(module, '_get_client', return_value='client'), patch.object(module, '_translate_batch', side_effect=lambda client, batch: echo_batch(batch)), patch.object(module, '_translate_batch_ollama_adaptive', side_effect=echo_batch) if hasattr(module, '_translate_batch_ollama_adaptive') else patch.object(module, '_translate_batch_ollama', side_effect=echo_batch):
                    results, in_tokens, out_tokens = module.translate_segments(source)
                    self.assertNotIn('__SACRED_', results[0]['text'])
                    self.assertEqual(results[0]['start'], 0.0)
                    self.assertEqual(results[0]['speaker'], 'SPEAKER_B')
                    self.assertEqual((in_tokens, out_tokens), (7, 8))
                    self.assertEqual(source[0]['text'], 'Allah and Musa AS')
            current = module.post_process_honorifics(*module.pre_process_honorifics('Allah ﷺ'))
            with patch.object(module, '_retranslate_shorter_unprotected', return_value='short'):
                self.assertEqual(module.retranslate_shorter({'text':current}, 5), current)
            # Mock only provider I/O: prove the real 401 branch calls Claude.
            with patch.object(module.httpx, 'post', return_value=type('Response', (), {'status_code':401})()), patch.object(module, '_get_client', return_value='client'), patch.object(module, '_translate_batch', return_value=([], 1, 2)) as fallback:
                self.assertEqual(module._translate_batch_ollama([]), ([], 1, 2))
                fallback.assert_called_once_with('client', [])

if __name__ == '__main__':
    unittest.main()
