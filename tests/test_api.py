from fastapi.testclient import TestClient
from uuid import uuid4

from app.core.config import settings
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
    assert isinstance(data["reply"], str)
    assert data["reply"].strip() != ""
    assert isinstance(data["global_emotion"], float)

    session_res = client.get(f"/session/private:{user_id}")
    assert session_res.status_code == 200
    session_data = session_res.json()
    assert session_data["user_id"] == user_id
    assert session_data["turn_count"] >= 1


def test_chat_returns_unavailable_when_kilo_unavailable() -> None:
    origin_provider = settings.llm.provider
    origin_api_key = settings.llm.api_key
    origin_admin = settings.onebot.debug_only_user_id
    try:
        settings.llm.provider = "kilo"
        settings.llm.api_key = ""
        settings.onebot.debug_only_user_id = 24680

        user_id = f"u_{uuid4().hex[:8]}"
        response = client.post("/chat", json={"user_id": user_id, "message": "你还在吗"})
        assert response.status_code == 200
        data = response.json()
        assert data["reply"] == "当前不可用，请联系管理员（debug账号：24680）"
    finally:
        settings.llm.provider = origin_provider
        settings.llm.api_key = origin_api_key
        settings.onebot.debug_only_user_id = origin_admin
