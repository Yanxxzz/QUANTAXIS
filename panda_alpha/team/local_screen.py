"""Local-first routing using the existing hash-bound portfolio study engine.

Missing source evidence may select official validation. Invalid inputs and
implementation exceptions never silently become permission to spend credits.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..evaluation import check_window
from ..quality import assess_research_quality
from ..study import prepare_study, evaluate_study
from .contracts import canonical, digest, verify_artifacts
from .evidence import artifact, read_artifact


class LocalScreenBlocked(ValueError):
    pass


def local_first(board, task, config, registry):
    """Record one reproducible local route before any new remote job.

    The manifest belongs to one frozen request. A collector can supply cached
    panels, or explicit source-unavailability evidence; this never fetches paid
    data, executes candidate Python, changes definitions, or grants funding.
    """
    policy = config.get('local_screen', {})
    if policy.get('enabled') is not True:
        return None  # Frozen older deployments keep their original contract.
    binding = policy.get('inputs', {}).get(task['fingerprint'])
    if binding is None:
        raise LocalScreenBlocked('Local-first requires a bound input or source-unavailability manifest')
    manifest = read_artifact(binding)
    if any(manifest.get(k) != task[k] for k in ('fingerprint', 'request_hash')):
        raise LocalScreenBlocked('Local input manifest does not match the frozen request')
    if not manifest.get('artifacts'):
        raise LocalScreenBlocked('Local source manifest requires evidence artifacts')
    verify_artifacts(manifest.get('artifacts', []))
    request = task['request']
    options = {'costs': config['admission']['one_way_costs'],
               'minimum_assets': config['diversity']['minimum_assets'],
               'allow_official_fallback': policy.get('allow_official_fallback') is True}
    key = digest({'manifest': binding, 'request_hash': task['request_hash'], 'options': options,
                  'implementation': artifact(Path(__file__)),
                  'study': artifact(Path(__file__).parents[1] / 'study.py'),
                  'quality': artifact(Path(__file__).parents[1] / 'quality.py'),
                  'attribution': artifact(Path(__file__).parents[1] / 'attribution.py'),
                  'performance': artifact(Path(__file__).parents[1] / 'evaluation.py')})
    event_id = 'local-screen:' + key
    previous = next((e for e in board.events(task['task_id']) if e['event_id'] == event_id), None)
    if previous:
        report = read_artifact(previous['payload']['report'])
    else:
        report = {'fingerprint': task['fingerprint'], 'request_hash': task['request_hash'],
                  'input_manifest': binding, 'official_runs': 0, 'pool_admission': False,
                  'scope': 'fixed-definition supplied-source local research; no official or full PIT certification'}
        unavailable = manifest.get('availability') == 'unavailable'
        if unavailable:
            if not manifest.get('reason') or not manifest.get('artifacts'):
                raise LocalScreenBlocked('Missing-data fallback requires a reason and source evidence')
            report.update(local_status='data_unavailable', reason=manifest['reason'])
        elif manifest.get('availability') == 'ready':
            # A raw snapshot is useful for signal checks, but cannot manufacture
            # continuous total-return labels or historical financial disclosure.
            if manifest.get('price_adjustment') not in {'qfq', 'hfq'}:
                unavailable = True
                report.update(local_status='data_unavailable', reason='Adjusted return-price evidence unavailable')
            elif task['owner'] == 'fundamental' and manifest.get('financial_pit_verified') is not True:
                unavailable = True
                report.update(local_status='data_unavailable', reason='Local historical financial PIT evidence unavailable')
            else:
                quotes = pd.read_parquet(_verified_path(manifest['quotes']))
                # AXIS adds adjustment-factor dates as Timestamp metadata.
                # Preserve their values in JSON-hashable form for the existing
                # study engine; the original parquet remains hash-bound.
                for column in quotes:
                    if pd.api.types.is_datetime64_any_dtype(quotes[column]):
                        quotes[column] = quotes[column].map(lambda value: value.isoformat() if pd.notna(value) else None)
                values = pd.read_parquet(_verified_path(manifest['values']))
                calendar = read_artifact(manifest['calendar'])
                if manifest.get('calendar_verified') is not True:
                    raise LocalScreenBlocked('Independent calendar evidence must be explicit')
                if 'adjustment' not in quotes or set(quotes.adjustment.dropna()) != {manifest['price_adjustment']}:
                    raise LocalScreenBlocked('Quote adjustment metadata disagrees with the source manifest')
                check_window(request['window']['start'], request['window']['end'],
                             config['research'].get('sealed_windows', []), str(pd.to_datetime(quotes.date).min().date()))
                protocol = {'candidate_id': request['candidate']['candidate_id'],
                            'direction': request['candidate']['direction'], 'cycle': request['cycle'],
                            'groups': request['groups'], 'min_assets': options['minimum_assets'],
                            'costs': options['costs'], 'calendar': calendar['dates'], 'calendar_verified': True,
                            'window': {'decisions_start': request['window']['start'],
                                       'decisions_end': request['window']['end'], 'holding_end': request['window']['end']}}
                registry.record(request['candidate'], request['window'], request['cycle'], request['groups'])
                receipt = {'manifest': binding, 'source_scope': manifest.get('source_scope', 'supplied scope only')}
                preparation = prepare_study(protocol, values, quotes, source_receipt=receipt)
                study = evaluate_study(preparation, protocol, values, quotes, source_receipt=receipt)
                quality = assess_research_quality(study)
                report.update(local_status=quality['status'], preparation=preparation, study=study, quality=quality)
                if quality['status'] == 'execution_pending' and any(
                    not str(e.get('error', '')).startswith(('unknown_', 'suspension_has_no_previous_mark:'))
                    for e in study.get('errors', [])
                ):
                    raise LocalScreenBlocked('Local execution implementation/input error requires repair, not paid fallback')
                unavailable = quality['status'] in {'source_pending', 'execution_pending'}
        else:
            raise LocalScreenBlocked('Local manifest availability must be ready or unavailable')
        if unavailable:
            report['route'] = 'official_fallback' if options['allow_official_fallback'] else 'await_local_data'
        elif report['local_status'] in {'fixed_definition_weak', 'redundant_information'}:
            report['route'] = 'local_reject'
        else:
            report['route'] = 'official_validation'
        path = Path(policy['output_dir']) / (key + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = canonical(report)
        if path.exists() and path.read_text(encoding='utf-8') != encoded:
            raise LocalScreenBlocked('Local study report identity already has different content')
        path.write_text(encoded, encoding='utf-8')
        board.event(task['task_id'], 'steward', 'local_screen',
                    {'report': artifact(path), 'route': report['route'], 'local_status': report['local_status']}, event_id=event_id)
    if report['route'] in {'local_reject', 'await_local_data'}:
        raise LocalScreenBlocked('Local-first route: ' + report['route'])
    return report


def _verified_path(binding):
    verify_artifacts([binding])
    return binding['path']
