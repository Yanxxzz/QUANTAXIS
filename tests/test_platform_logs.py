import unittest
from panda_alpha.platform_logs import collect_run_logs


class RunLogTests(unittest.TestCase):
    def test_error_after_first_five_rows_is_collected(self):
        calls = []
        def fetch(cursor, limit):
            calls.append(cursor)
            if cursor is None:
                data = {'logs': [{'sequence': n, 'message': 'ok', 'level': 'INFO'} for n in range(1, 6)],
                        'has_more': True, 'next_sequence': 6}
            else:
                data = {'logs': [{'sequence': 6, 'level': 'ERROR', 'work_node_id': 'analysis',
                                 'message': 'factor analysis failed', 'error_detail': 'empty samples', 'user_id': 'private'}],
                        'has_more': False}
            return {'code': 0, 'data': data}
        result = collect_run_logs(fetch, page_size=5)
        self.assertEqual(calls, [None, 6])
        self.assertTrue(result['complete'])
        self.assertEqual(result['errors'][0]['error_detail'], 'empty samples')
        self.assertNotIn('user_id', result['logs'][-1])

    def test_legacy_node_map_still_supported(self):
        result = collect_run_logs(lambda *_: {'code': 0, 'data': {'nodes': {'a': {'title': 'factor', 'error': 'NameError'}}}})
        self.assertEqual(result['errors'][0]['error_message'], 'NameError')

    def test_error_detail_is_preserved_with_nonerror_level(self):
        result = collect_run_logs(lambda *_: {'code': 0, 'data': {'logs': [{'sequence': 1, 'level': 'INFO', 'error_detail': {'traceback': 'bad'}}]}})
        self.assertEqual(result['errors'][0]['error_detail'], {'traceback': 'bad'})

    def test_nonadvancing_cursor_does_not_loop(self):
        def fetch(cursor, _):
            return {'code': 0, 'data': {'logs': [], 'has_more': True, 'next_sequence': 2}}
        with self.assertRaisesRegex(ValueError, 'nonadvancing'):
            collect_run_logs(fetch)

    def test_bounded_pages_report_incomplete(self):
        def fetch(cursor, _):
            return {'code': 0, 'data': {'logs': [], 'has_more': True, 'next_sequence': (cursor or 0) + 1}}
        result = collect_run_logs(fetch, max_pages=2)
        self.assertFalse(result['complete'])
        self.assertEqual(result['next_sequence'], 2)

    def test_failed_api_response_is_not_empty_success(self):
        with self.assertRaises(RuntimeError):
            collect_run_logs(lambda *_: {'code': 403, 'data': None})


if __name__ == '__main__':
    unittest.main()
