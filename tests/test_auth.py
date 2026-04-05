"""P8-T13: Auth unit tests — JWT, password hashing, roles, bootstrap."""

import pytest
from datetime import datetime
from src.auth.service import AuthService
from src.models.base import User, UserRole
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def auth_setup(tmp_path):
    state = SQLiteStateStore(db_path=str(tmp_path / "auth.db"))
    await state.initialize()
    auth = AuthService(
        state=state,
        secret_key="test-secret-key",
        access_token_minutes=5,
        refresh_token_days=1,
    )
    yield auth, state
    await state.close()


async def test_hash_and_verify_password(auth_setup):
    auth, _ = auth_setup
    hashed = auth.hash_password("mypassword")
    assert hashed != "mypassword"
    assert auth.verify_password("mypassword", hashed) is True
    assert auth.verify_password("wrong", hashed) is False


async def test_create_access_token(auth_setup):
    auth, _ = auth_setup
    user = User(user_id="u1", username="test", email="t@t.com", role=UserRole.DEVELOPER)
    token = auth.create_access_token(user)
    assert isinstance(token, str)
    assert len(token) > 20


async def test_decode_valid_token(auth_setup):
    auth, _ = auth_setup
    user = User(user_id="u1", username="test", email="t@t.com", role=UserRole.ADMIN)
    token = auth.create_access_token(user)
    payload = auth.decode_token(token)
    assert payload["sub"] == "u1"
    assert payload["username"] == "test"
    assert payload["role"] == "admin"


async def test_decode_invalid_token(auth_setup):
    auth, _ = auth_setup
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        auth.decode_token("invalid.token.here")


async def test_authenticate_valid_credentials(auth_setup):
    auth, state = auth_setup
    user = User(user_id="u2", username="alice", email="a@t.com", role=UserRole.DEVELOPER)
    pw_hash = auth.hash_password("secret123")
    await state.create_user(user, pw_hash)

    authed_user, token = await auth.authenticate("alice", "secret123")
    assert authed_user.username == "alice"
    assert isinstance(token, str)


async def test_authenticate_invalid_password(auth_setup):
    auth, state = auth_setup
    user = User(user_id="u3", username="bob", email="b@t.com", role=UserRole.VIEWER)
    pw_hash = auth.hash_password("correct")
    await state.create_user(user, pw_hash)

    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        await auth.authenticate("bob", "wrong")


async def test_authenticate_nonexistent_user(auth_setup):
    auth, _ = auth_setup
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        await auth.authenticate("nobody", "pass")


async def test_bootstrap_admin_first_run(auth_setup):
    auth, state = auth_setup
    password = await auth.bootstrap_admin()
    assert password is not None
    assert len(password) > 10

    users = await state.list_users()
    assert len(users) == 1
    assert users[0].username == "admin"
    assert users[0].role == UserRole.ADMIN
    assert users[0].must_change_password is True


async def test_bootstrap_admin_idempotent(auth_setup):
    auth, state = auth_setup
    pw1 = await auth.bootstrap_admin()
    pw2 = await auth.bootstrap_admin()
    assert pw1 is not None
    assert pw2 is None  # second call returns None — admin already exists
    users = await state.list_users()
    assert len(users) == 1


async def test_create_refresh_token(auth_setup):
    auth, _ = auth_setup
    token = auth.create_refresh_token()
    assert isinstance(token, str)
    assert len(token) > 20


async def test_role_viewer_cannot_submit():
    """Viewer role should not have requests:create permission."""
    # This is a config-level test — verify the role definitions
    from src.config.loader import ConfigLoader
    config = ConfigLoader()
    config.load_all()
    roles = config.project.get("auth", {}).get("roles", {})
    viewer_perms = roles.get("viewer", {}).get("permissions", [])
    assert "requests:create" not in viewer_perms

    developer_perms = roles.get("developer", {}).get("permissions", [])
    assert "requests:create" in developer_perms
