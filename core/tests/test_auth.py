def test_login_success(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin_test_123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["user"]["username"] == "admin"
    assert data["user"]["role"] == "admin"


def test_login_invalid_password(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong_password!"},
    )
    assert response.status_code == 401


def test_get_current_user_profile(client, admin_headers):
    response = client.get("/api/v1/auth/me", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "admin"
    assert data["email"] == "admin@example.com"


def test_create_and_use_api_token(client, admin_headers):
    # 1. Create API token
    create_resp = client.post(
        "/api/v1/auth/tokens",
        json={"name": "test-ci-token", "expires_in_days": 30},
        headers=admin_headers,
    )
    assert create_resp.status_code == 201
    tok_data = create_resp.json()
    raw_token = tok_data["raw_token"]
    assert raw_token.startswith("zds_")

    # 2. Use API token to authenticate
    token_headers = {"Authorization": f"Bearer {raw_token}"}
    me_resp = client.get("/api/v1/auth/me", headers=token_headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == "admin"


def test_rbac_access_control(client, viewer_headers, admin_headers):
    # Viewer tries to create a user (admin only) -> 403 Forbidden
    resp = client.post(
        "/api/v1/auth/users",
        json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "password123!",
            "role": "viewer",
        },
        headers=viewer_headers,
    )
    assert resp.status_code == 403

    # Admin creates user -> 201 Created
    resp_admin = client.post(
        "/api/v1/auth/users",
        json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "password123!",
            "role": "viewer",
        },
        headers=admin_headers,
    )
    assert resp_admin.status_code == 201
    assert resp_admin.json()["username"] == "newuser"


def test_read_only_access_and_legacy_roles(client, viewer_headers, operator_headers):
    for headers in (viewer_headers, operator_headers):
        assert client.get("/api/v1/auth/me", headers=headers).json()["role"] == "viewer"
        for route in ("devices", "devices/stats", "metrics/probe-latency", "alerts", "events"):
            assert client.get("/api/v1/" + route, headers=headers).status_code == 200
        for route in ("auth/users", "auth/tokens", "checks", "check-templates", "devices/1", "groups", "collectors"):
            assert client.get("/api/v1/" + route, headers=headers).status_code == 403
        assert client.post("/api/v1/alerts/1/acknowledge", headers=headers, json={"comment": "test"}).status_code == 403
        assert client.post("/api/v1/alerts/1/resolve", headers=headers).status_code == 403
        assert client.patch("/api/v1/auth/users/1", headers=headers, json={"role": "admin"}).status_code == 403


def test_admin_changes_access_and_password_and_revokes_old_sessions(client, admin_headers):
    from secrets import token_hex

    first, second = token_hex(16), token_hex(16)
    account = {"username": "managed-account", "email": "managed@example.com", "password": first}
    assert client.post("/api/v1/auth/users", headers=admin_headers, json={**account, "role": "operator"}).status_code == 422
    created = client.post("/api/v1/auth/users", headers=admin_headers, json=account)
    assert created.status_code == 201
    account_route = f"/api/v1/auth/users/{created.json()['id']}"

    def login(password):
        return client.post("/api/v1/auth/login", json={"username": account["username"], "password": password})

    previous = {"Authorization": "Bearer " + login(first).json()["access_token"]}
    updated = client.patch(account_route, headers=admin_headers, json={"role": "admin", "password": second})
    assert updated.status_code == 200
    assert updated.json()["role"] == "admin"
    assert second not in updated.text
    assert login(first).status_code == 401
    assert client.get("/api/v1/auth/me", headers=previous).status_code == 401
    new_headers = {"Authorization": "Bearer " + login(second).json()["access_token"]}
    assert client.get("/api/v1/auth/users", headers=new_headers).status_code == 200
    assert client.patch(account_route, headers=new_headers, json={"role": "viewer"}).status_code == 409
    assert client.patch(account_route, headers=new_headers, json={"password": first}).status_code == 400
    assert client.patch(account_route, headers=new_headers, json={"password": first, "current_password": second}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=new_headers).status_code == 401
    assert login(first).status_code == 200
    assert client.patch(account_route, headers=admin_headers, json={"is_active": False}).status_code == 200
    assert login(first).status_code == 403
