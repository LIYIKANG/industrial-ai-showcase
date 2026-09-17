"""Customer-facing input must reach the calculations shown in the UI."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.core.project_store import ProjectStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'projects', ProjectStore(tmp_path / 'qa.db'))
    return TestClient(main.app)


def test_ranking_uses_customer_weights(client):
    today = date.today()
    payload = {
        'materials': [{'id': 'A', 'category': 'finished', 'special_control_level': 2}],
        'inventory': [{'material_id': 'A', 'quantity': 1000}],
        'open_orders': [
            {'id': 'SMALL-VIP', 'material_id': 'A', 'quantity': 10, 'customer_tier': 'premium', 'due_date': today.isoformat()},
            {'id': 'LARGE-BASE', 'material_id': 'A', 'quantity': 500, 'customer_tier': 'base', 'due_date': today.isoformat()},
        ],
        'weights': {'priority': 1, 'quantity': 0, 'lateness': 0},
    }
    first = client.post('/api/atp/rank', json=payload).json()
    assert first['ranking'][0]['order_id'] == 'SMALL-VIP'
    payload['weights'] = {'priority': 0, 'quantity': 1, 'lateness': 0}
    second = client.post('/api/atp/rank', json=payload).json()
    assert second['ranking'][0]['order_id'] == 'LARGE-BASE'
    assert second['weights'] == payload['weights']


def test_ranking_passes_epsilon(client):
    result = client.post('/api/atp/rank', json={
        'strategy': 'epsilon_constraint', 'epsilon': {'max_lateness_days': 17},
    })
    assert result.status_code == 200
    assert result.json()['epsilon']['max_lateness_days'] == 17


def test_uploaded_dataset_is_returned_for_subsequent_ui_calculation(client):
    result = client.post('/api/atp/upload', data={'project_id': 'qa-upload'}, files={
        'materials': ('materials.csv', b'id,name,category,special_control_level\nQA-NEW,QA material,finished,2\n', 'text/csv'),
        'inventory': ('inventory.csv', b'material_id,quantity\nQA-NEW,1234\n', 'text/csv'),
    })
    assert result.status_code == 200
    data = result.json()['data']
    assert [row['id'] for row in data['materials']] == ['QA-NEW']
    computed = client.post('/api/atp/compute', json=data).json()
    assert computed['summary']['materials'] == 1
    assert computed['matrix'][0]['material_id'] == 'QA-NEW'


def test_imported_chemical_project_has_a_valid_quantity_model(client):
    from backend.core.local_solver import solve_local
    from backend.core.model_validator import validate_model

    response = client.post('/api/atp/import', json={
        'project_id': 'qa-model',
        'materials': [{'id': 'A', 'category': 'finished', 'special_control_level': 2}],
        'inventory': [{'material_id': 'A', 'quantity': 100}],
        'open_orders': [{'id': 'ORDER', 'material_id': 'A', 'quantity': 50, 'customer_tier': 'base'}],
    })
    assert response.status_code == 200
    payload = client.get('/api/projects/qa-model').json()['payload']
    model = payload['ontology']['problem']
    assert validate_model(model)['valid']
    assert model['constraints'][0]['coefficients'] == {'x_ORDER': 1.0}
    result = solve_local(model)
    assert result['objective_value'] == 50
