"""Regression for the actual EOF document-loss failure; no source calls."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest


class ReaderOutputTest(unittest.TestCase):
    def test_noninteractive_eof_emits_all_acquired_originals(self):
        spec = importlib.util.spec_from_file_location('q1_output_v2', Path(__file__).with_name('read_q1_v2.py'))
        runtime = importlib.util.module_from_spec(spec); spec.loader.exec_module(runtime)
        documents = [{'metadata': {'symbol': str(i), 'announcement_id': str(i)},
                      'page_texts': ['第一页', '第二页'],
                      'full_text': f'@@SOURCE_PAGE:1@@\n甲{i}\n@@SOURCE_PAGE:2@@\n乙{i}\n',
                      'PDF_sha256': str(i), 'text_sha256': str(i)} for i in range(3)]
        previous = sys.stdin
        captured = io.StringIO()
        try:
            sys.stdin = io.StringIO('')
            with contextlib.redirect_stdout(captured):
                runtime.emit_documents(documents)
        finally:
            sys.stdin = previous
        lines = captured.getvalue().splitlines()
        headers = [json.loads(line) for line in lines if line.startswith('{')]
        self.assertEqual([x['index'] for x in headers], [0, 1, 2])
        for i in range(3):
            self.assertIn(documents[i]['full_text'], captured.getvalue())


if __name__ == '__main__':
    unittest.main()
