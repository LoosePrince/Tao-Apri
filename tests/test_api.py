from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_llm_models_endpoint() -> None:
    response = client.get("/llm/models")
    assert response.status_code == 200
    data = response.json()
    assert "provider" in data
    assert "models" in data
    assert isinstance(data["models"], list)


def test_chat_and_session_flow() -> None:
    user_id = f"u_{uuid4().hex[:8]}"
    response = client.post("/chat", json={"user_id": user_id, "message": "我今天很开心，谢谢你"})
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == user_id
    assert "状态" in data["reply"]
    assert isinstance(data["global_emotion"], float)

    session_res = client.get(f"/session/{user_id}")
    assert session_res.status_code == 200
    session_data = session_res.json()
    assert session_data["user_id"] == user_id
    assert session_data["turn_count"] >= 1
