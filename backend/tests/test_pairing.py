import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DATABASE_URL', 'sqlite:///./test-pairing.db')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
os.environ.setdefault('JWT_SECRET', 'test-secret')


class _RedisStub:
    """Only the two operations the API/worker boundary needs for these tests."""
    def __init__(self, *a, **k): self.queue = []

    def incr(self, key): return 1

    def expire(self, key, ttl): return True

    def rpush(self, key, value): self.queue.append(value); return len(self.queue)

    @classmethod
    def from_url(cls, *a, **k): return redis_stub

    def blpop(self, key, timeout=0): return None


redis_stub = _RedisStub()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / 'research.db'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{db_path}')
    monkeypatch.setenv('SEED_DEMO', 'false')
    for module in [m for m in list(sys.modules) if m == 'src' or m.startswith('src.')]:
        del sys.modules[module]
    import src.middlewares.rate_limit as rate_limit
    monkeypatch.setattr(rate_limit, 'redis', redis_stub)
    import src.services.runs as run_service
    monkeypatch.setattr(run_service, 'Redis', _RedisStub)
    from src.main import app
    from src.config.database import Session
    from src.models.user import User
    from src.utils.security import hash_password
    with TestClient(app) as test_client:
        with Session() as db:
            for role in ('admin', 'analyst', 'viewer'):
                db.add(User(email=f'{role}@test.local', role=role, password_hash=hash_password('pw123456')))
            db.commit()
        yield test_client


def _token(client, role):
    response = client.post('/auth/login', json={'email': f'{role}@test.local', 'password': 'pw123456'})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['token']}


def _study(client, headers, name='配对测试批次'):
    response = client.post('/studies', json={'name': name, 'source_filename': 'press.csv'}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()['id']


def _reading(client, headers, study, process, label, value, at):
    response = client.post(f'/studies/{study}/readings',
                           json={'process': process, 'label': label, 'value': value, 'measured_at': at},
                           headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _preview(client, headers, study, gap):
    response = client.post(f'/studies/{study}/pairing/previews', json={'max_gap_seconds': gap}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_candidates_respect_gap_and_use_own_numbering(client):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    _reading(client, headers, study, 'before', 'OLD-01', 1.1, '2026-11-01T09:00:00Z')
    _reading(client, headers, study, 'after', 'NEW-07', 1.3, '2026-11-01T09:00:20Z')
    _reading(client, headers, study, 'after', 'NEW-08', 1.4, '2026-11-01T09:10:00Z')

    preview = _preview(client, headers, study, 30)
    result = preview['result']
    assert result['maximum_pairs'] == 1
    assert result['common_pairs'] == 1
    assert result['pairs'][0]['before_label'] == 'OLD-01'
    assert result['pairs'][0]['after_label'] == 'NEW-07'
    unmatched = {x['label']: x for x in result['unpaired']}
    assert unmatched['NEW-08']['state'] == 'no_candidate'
    assert '时差' in unmatched['NEW-08']['reason']


def test_tie_keeps_only_pairs_common_to_every_optimum(client):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    _reading(client, headers, study, 'before', 'B1', 1.0, '2026-11-01T10:00:00Z')
    _reading(client, headers, study, 'before', 'B2', 1.0, '2026-11-01T10:00:20Z')
    _reading(client, headers, study, 'after', 'A1', 1.0, '2026-11-01T10:00:10Z')
    _reading(client, headers, study, 'after', 'A2', 1.0, '2026-11-01T10:00:10Z')

    preview = _preview(client, headers, study, 60)
    result = preview['result']
    # Both readings can be matched two ways with the same total gap: no pair is common.
    assert result['maximum_pairs'] == 2
    assert result['common_pairs'] == 0
    assert result['pairs'] == []
    assert all(x['state'] == 'ambiguous' for x in result['readings'])
    assert {x['label'] for x in result['unpaired']} == {'B1', 'B2', 'A1', 'A2'}


def test_unique_optimum_confirms_all_pairs(client):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    _reading(client, headers, study, 'before', 'B1', 1.0, '2026-11-01T10:00:00Z')
    _reading(client, headers, study, 'before', 'B2', 1.0, '2026-11-01T10:20:00Z')
    _reading(client, headers, study, 'after', 'A1', 1.0, '2026-11-01T10:01:00Z')
    _reading(client, headers, study, 'after', 'A2', 1.0, '2026-11-01T10:18:20Z')

    result = _preview(client, headers, study, 600)['result']
    assert result['maximum_pairs'] == 2
    assert result['common_pairs'] == 2
    assert {(p['before_label'], p['after_label']) for p in result['pairs']} == {('B1', 'A1'), ('B2', 'A2')}


def test_maximises_pairs_before_gap_total(client):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    _reading(client, headers, study, 'before', 'B1', 1.0, '2026-11-01T10:00:00Z')
    _reading(client, headers, study, 'before', 'B2', 1.0, '2026-11-01T10:00:30Z')
    _reading(client, headers, study, 'after', 'A1', 1.0, '2026-11-01T10:00:05Z')

    result = _preview(client, headers, study, 60)['result']
    assert result['maximum_pairs'] == 1          # one-to-one, not two readings on one partner
    assert result['minimum_total_gap_seconds'] == 5
    assert result['pairs'][0]['before_label'] == 'B1'


def test_confirm_switches_batch_and_new_run_freezes_pairs(client, monkeypatch):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    _reading(client, headers, study, 'before', 'B1', 4.0, '2026-11-01T10:00:00Z')
    _reading(client, headers, study, 'before', 'B2', 5.0, '2026-11-01T10:20:00Z')
    _reading(client, headers, study, 'after', 'A1', 3.6, '2026-11-01T10:01:00Z')

    assert client.get(f'/studies/{study}/pairing', headers=headers).json() is None
    preview = _preview(client, headers, study, 600)
    assert preview['result']['common_pairs'] == 1

    confirmed = client.post(f'/pairing/previews/{preview["id"]}/confirm', headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body['switched'] is True
    assert body['active_pairing']['preview_id'] == preview['id']

    active = client.get(f'/studies/{study}/pairing', headers=headers).json()
    assert active['preview_id'] == preview['id']
    assert len(active['pairs']) == 1

    second = _preview(client, headers, study, 600)
    switched = client.post(f'/pairing/previews/{second["id"]}/confirm', headers=headers)
    assert switched.status_code == 200
    assert client.get(f'/studies/{study}/pairing', headers=headers).json()['preview_id'] == second['id']

    run = client.post('/runs', json={'study_id': study, 'name': '冻结配对', 'parameters': {}}, headers=headers)
    assert run.status_code == 200, run.text
    assert run.json()['pairing_preview_id'] == second['id']
    assert len(run.json()['snapshot']) == 1


def test_concurrent_reading_change_invalidates_preview_and_keeps_old_pairing(client):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    _reading(client, headers, study, 'before', 'B1', 4.0, '2026-11-01T10:00:00Z')
    _reading(client, headers, study, 'after', 'A1', 3.6, '2026-11-01T10:01:00Z')
    first = _preview(client, headers, study, 600)
    client.post(f'/pairing/previews/{first["id"]}/confirm', headers=headers)

    stale = _preview(client, headers, study, 600)
    rows = client.get(f'/studies/{study}/readings', headers=headers).json()
    target = next(x for x in rows if x['label'] == 'A1')
    edited = client.put(f'/studies/{study}/readings/{target["id"]}',
                        json={'process': 'after', 'label': 'A1', 'value': 3.9, 'measured_at': target['measured_at']},
                        headers=headers)
    assert edited.status_code == 200

    blocked = client.post(f'/pairing/previews/{stale["id"]}/confirm', headers=headers)
    assert blocked.status_code == 409
    assert '作废' in blocked.json()['detail']
    assert '保持不变' in blocked.json()['detail']
    assert client.get(f'/studies/{study}/pairing', headers=headers).json()['preview_id'] == first['id']
    assert client.get(f'/pairing/previews/{stale["id"]}', headers=headers).json()['status'] == 'invalidated'


def test_viewer_cannot_confirm_or_edit(client):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    _reading(client, headers, study, 'before', 'B1', 4.0, '2026-11-01T10:00:00Z')
    _reading(client, headers, study, 'after', 'A1', 3.6, '2026-11-01T10:01:00Z')
    viewer = _token(client, 'viewer')
    assert client.get(f'/studies/{study}/readings', headers=viewer).status_code == 200
    assert client.post(f'/studies/{study}/pairing/previews', json={'max_gap_seconds': 60}, headers=viewer).status_code == 403
    preview = _preview(client, headers, study, 60)
    assert client.post(f'/pairing/previews/{preview["id"]}/confirm', headers=viewer).status_code == 403


def test_invalid_timestamps_and_duplicate_labels_are_rejected(client):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    bad = client.post(f'/studies/{study}/readings',
                      json={'process': 'before', 'label': 'B1', 'value': 1.0, 'measured_at': 'yesterday'},
                      headers=headers)
    assert bad.status_code == 422
    _reading(client, headers, study, 'before', 'B1', 1.0, '2026-11-01T10:00:00Z')
    duplicate = client.post(f'/studies/{study}/readings',
                            json={'process': 'before', 'label': 'B1', 'value': 2.0, 'measured_at': '2026-11-01T10:00:00Z'},
                            headers=headers)
    assert duplicate.status_code == 409
    negative = client.post(f'/studies/{study}/readings',
                           json={'process': 'after', 'label': 'A1', 'value': -1, 'measured_at': '2026-11-01T10:00:00Z'},
                           headers=headers)
    assert negative.status_code == 422


def test_batch_upload_validates_atomically(client):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    good = {'process': 'before', 'rows': [
        {'label': 'B1', 'value': 4.0, 'measured_at': '2026-11-01T10:00:00+08:00'},
        {'label': 'B2', 'value': 5.0, 'measured_at': '2026-11-01T10:20:00+08:00'},
    ]}
    assert client.post(f'/studies/{study}/readings/batch', json=good, headers=headers).json()['saved'] == 2
    rows = client.get(f'/studies/{study}/readings', headers=headers).json()
    # +08:00 input is normalised to UTC so gap arithmetic is timezone-safe.
    assert rows[0]['measured_at'] == '2026-11-01T02:00:00.000000Z'

    duplicate_in_upload = {'process': 'after', 'rows': [
        {'label': 'A1', 'value': 1, 'measured_at': '2026-11-01T10:00:00Z'},
        {'label': 'A1', 'value': 2, 'measured_at': '2026-11-01T10:00:00Z'},
    ]}
    assert client.post(f'/studies/{study}/readings/batch', json=duplicate_in_upload, headers=headers).status_code == 422
    clash = {'process': 'before', 'rows': [{'label': 'B2', 'value': 1, 'measured_at': '2026-11-01T10:00:00Z'}]}
    assert client.post(f'/studies/{study}/readings/batch', json=clash, headers=headers).status_code == 409
    rejects = {'process': 'before', 'rows': [
        {'label': 'B9', 'value': 1, 'measured_at': '2026-11-01T10:00:00Z'},
        {'label': 'B10', 'value': 1, 'measured_at': 'not-a-time'},
    ]}
    assert client.post(f'/studies/{study}/readings/batch', json=rejects, headers=headers).status_code == 422
    labels = [x['label'] for x in client.get(f'/studies/{study}/readings', headers=headers).json()]
    assert 'B9' not in labels                       # atomic: the valid row did not land either
    assert len(labels) == 2


def test_analysis_requires_confirmation_once_readings_exist(client):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    _reading(client, headers, study, 'before', 'B1', 4.0, '2026-11-01T10:00:00Z')
    _reading(client, headers, study, 'after', 'A1', 3.6, '2026-11-01T10:01:00Z')
    blocked = client.post('/runs', json={'study_id': study, 'name': '未确认', 'parameters': {}}, headers=headers)
    assert blocked.status_code == 409

    preview = _preview(client, headers, study, 600)
    client.post(f'/pairing/previews/{preview["id"]}/confirm', headers=headers)
    row = next(x for x in client.get(f'/studies/{study}/readings', headers=headers).json() if x['label'] == 'A1')
    client.put(f'/studies/{study}/readings/{row["id"]}',
               json={'process': 'after', 'label': 'A1', 'value': 3.8, 'measured_at': row['measured_at']}, headers=headers)
    stale = client.post('/runs', json={'study_id': study, 'name': '旧配对', 'parameters': {}}, headers=headers)
    assert stale.status_code == 409
    assert '重新预览' in stale.json()['detail']


def test_frozen_run_survives_later_reconfirmation(client):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    _reading(client, headers, study, 'before', 'B1', 4.0, '2026-11-01T10:00:00Z')
    _reading(client, headers, study, 'after', 'A1', 3.6, '2026-11-01T10:01:00Z')
    first = _preview(client, headers, study, 600)
    client.post(f'/pairing/previews/{first["id"]}/confirm', headers=headers)
    run = client.post('/runs', json={'study_id': study, 'name': '冻结', 'parameters': {}}, headers=headers).json()

    _reading(client, headers, study, 'before', 'B2', 4.4, '2026-11-01T10:05:00Z')
    _reading(client, headers, study, 'after', 'A2', 3.9, '2026-11-01T10:06:00Z')
    second = _preview(client, headers, study, 600)
    client.post(f'/pairing/previews/{second["id"]}/confirm', headers=headers)

    frozen = client.get(f'/runs/{run["id"]}', headers=headers).json()
    assert frozen['pairing_preview_id'] == first['id']
    assert frozen['snapshot'] == run['snapshot']
    assert len(frozen['snapshot']) == 1


def test_ambiguous_reading_used_by_every_optimum_reports_why(client):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    # B1 is the only reading that can reach A1 (the others are outside the window),
    # but B1 can pair with A1 or A2, so no partner is common to every optimum.
    _reading(client, headers, study, 'before', 'B1', 1.0, '2026-11-01T10:00:00Z')
    _reading(client, headers, study, 'after', 'A1', 1.0, '2026-11-01T10:00:00Z')
    _reading(client, headers, study, 'after', 'A2', 1.0, '2026-11-01T10:00:00Z')

    result = _preview(client, headers, study, 10)['result']
    assert result['maximum_pairs'] == 1
    assert result['common_pairs'] == 0
    states = {x['label']: x for x in result['readings']}
    assert states['B1']['state'] == 'ambiguous'
    assert '配对编号不唯一' in states['B1']['reason']
    assert states['B1']['optimal_partners'] == ['A1', 'A2']
    assert states['A1']['reason'] in ('存在同样最优的配对方案不使用该读数，当前配对并非所有最优方案共有',)


def test_confirm_rejected_when_no_pair_is_common(client):
    headers = _token(client, 'analyst')
    study = _study(client, headers)
    _reading(client, headers, study, 'before', 'B1', 1.0, '2026-11-01T10:00:00Z')
    _reading(client, headers, study, 'after', 'A1', 1.0, '2026-11-01T10:00:00Z')
    _reading(client, headers, study, 'after', 'A2', 1.0, '2026-11-01T10:00:00Z')
    preview = _preview(client, headers, study, 10)
    blocked = client.post(f'/pairing/previews/{preview["id"]}/confirm', headers=headers)
    assert blocked.status_code == 409
    assert client.get(f'/studies/{study}/pairing', headers=headers).json() is None
    assert client.get(f'/pairing/previews/{preview["id"]}', headers=headers).json()['status'] == 'preview'
