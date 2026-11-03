from models import ChatMessage, ChatSession


def test_chat_rejects_other_customers_session_before_any_write(db, auth_client):
    session = ChatSession(
        session_id="private-other-customer", user_id="another-owner", last_filters='{"max_price":1}'
    )
    db.add(session)
    db.commit()
    before = db.query(ChatMessage).count()
    response = auth_client.post(
        "/chat", json={"message": "gold rings", "session_id": session.session_id}
    )
    assert response.status_code == 404
    db.refresh(session)
    assert session.last_filters == '{"max_price":1}'
    assert db.query(ChatMessage).count() == before


def test_chat_same_owner_can_continue_saved_conversation(auth_client):
    payload = {"message": "gold rings", "session_id": "own-continuation-test"}
    assert auth_client.post("/chat", json=payload).status_code == 200
    payload["message"] = "below 20000"
    response = auth_client.post("/chat", json=payload)
    assert response.status_code == 200
    assert response.json()["applied_filters"]["max_price"] == 20000
