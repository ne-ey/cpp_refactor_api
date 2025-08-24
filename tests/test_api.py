from fastapi.testclient import TestClient
from main import app   

client = TestClient(app)

def test_ping():
    response = client.get("/ping")
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "pong"

def test_refactor_endpoint():
    response = client.post(
        "/refactor",
        json={"code": "int main(){return 0;}", "rules": {}}
    )
    assert response.status_code == 200
    data = response.json()
    assert "optimized_code" in data
    assert "suggestions" in data
