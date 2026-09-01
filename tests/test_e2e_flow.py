from pathlib import Path
import pytest
from app import app
from backend.database.db_manager import DatabaseManager


import json
from utils.settings_store import SETTINGS_PATH, get_settings


@pytest.fixture
def client():
    app.config['TESTING'] = True
    initial_settings = get_settings()
    with app.test_client() as client:
        yield client
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(initial_settings, f, indent=2)


def test_login_flow(client):
    response = client.post(
        '/login',
        data={
            'name': 'Test Candidate',
            'usn': '1CS23AI999',
            'subject': 'Cloud Computing',
            'exam_code': 'CS301',
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b'Test Candidate' in response.data
    assert b'1CS23AI999' in response.data


def test_status_endpoint_structure(client):
    response = client.get('/exam/status')
    assert response.status_code == 200
    data = response.get_json()
    assert 'running' in data
    assert 'subsystems' in data
    assert 'camera' in data['subsystems']
    assert 'face_detector' in data['subsystems']
    assert 'head_pose' in data['subsystems']
    assert 'person_detector' in data['subsystems']
    assert 'live_checks' in data


def test_settings_page_and_update(client):
    # GET settings
    get_res = client.get('/settings')
    assert get_res.status_code == 200
    assert b'Proctoring Settings' in get_res.data

    # POST new settings
    post_res = client.post(
        '/settings',
        data={
            'FACE_MISSING_SEC': '4.5',
            'LOOKING_AWAY_SEC': '2.5',
            'LOOKING_AWAY_ANGLE_DEG': '22',
            'LOOKING_AWAY_PITCH_DEG': '18',
            'PHONE_CONFIRM_FRAMES': '2',
            'MULTI_PERSON_CONFIRM_FRAMES': '2',
            'YOLO_CONFIDENCE': '0.55',
            'RISK_FACE_MISSING': '25',
            'RISK_LOOKING_AWAY': '20',
            'RISK_MULTIPLE_PERSON': '35',
            'RISK_PHONE_DETECTED': '45',
        },
    )
    assert post_res.status_code == 200
    assert b'Settings saved' in post_res.data


def test_reports_page(client):
    response = client.get('/reports')
    assert response.status_code == 200
    assert b'Exam Reports' in response.data
