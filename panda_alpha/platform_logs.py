"""Read owned run logs across the public sequence-paginated log API.

API contract: PandaAI-Tech/panda_quantflow workflow_routes.py, commit
688b90e74a738b84567efe622a2d9c1e5ce10e00. last_sequence is inclusive;
the server's next_sequence is the next page's inclusive start.
"""
from __future__ import annotations

import json
from typing import Callable


def collect_run_logs(get_page: Callable, *, page_size: int = 100,
                     max_pages: int = 10) -> dict:
    if not 1 <= page_size <= 1000 or max_pages < 1:
        raise ValueError('Invalid log pagination bounds')
    rows, errors, seen = [], [], set()
    cursor = None
    for page in range(1, max_pages + 1):
        response = get_page(cursor, page_size)
        if response.get('code') != 0:
            raise RuntimeError('PandaAI log request failed')
        data = response.get('data') or {}
        if not isinstance(data, dict):
            raise ValueError('Unknown PandaAI log response shape')
        if 'logs' not in data:
            # Older CLI-compatible node map.
            nodes = data.get('nodes', data)
            for node, info in nodes.items():
                if isinstance(info, dict) and info.get('error'):
                    errors.append({'node_uuid': node, 'node_title': info.get('title', ''),
                                   'error_message': str(info['error'])})
            return {'logs': rows, 'errors': errors, 'pages': page, 'complete': True}
        for item in data['logs']:
            # Never forward user IDs or authentication-related metadata.
            row = {k: item[k] for k in ('sequence', 'level', 'message', 'work_node_id',
                                      'error_detail', 'timestamp') if k in item}
            identity = (row.get('sequence'), row.get('work_node_id'), row.get('message'))
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(row)
            detail = row.get('error_detail')
            if detail or str(row.get('level', '')).upper() in ('ERROR', 'CRITICAL'):
                errors.append({'node_uuid': row.get('work_node_id'),
                               'sequence': row.get('sequence'),
                               'error_message': row.get('message', ''),
                               'error_detail': detail})
        if not data.get('has_more', False):
            return {'logs': rows, 'errors': errors, 'pages': page, 'complete': True}
        next_cursor = data.get('next_sequence')
        if type(next_cursor) is not int or (cursor is not None and next_cursor <= cursor):
            raise ValueError('Missing/nonadvancing log pagination cursor')
        cursor = next_cursor
    return {'logs': rows, 'errors': errors, 'pages': max_pages, 'complete': False,
            'next_sequence': cursor}
