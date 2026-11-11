import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zaddesh_core.database import Base, get_db
from zaddesh_core.main import app
from zaddesh_core.models import User, Role
from zaddesh_core.security import hash_password, create_access_token

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=test_engine)
    db = TestingSessionLocal()

    # Pre-seed roles
    admin_role = Role(name="admin", permissions=["*"])
    operator_role = Role(name="operator", permissions=["devices:*", "checks:*"])
    viewer_role = Role(name="viewer", permissions=["read:*"])
    db.add_all([admin_role, operator_role, viewer_role])
    db.flush()

    # Pre-seed users
    admin = User(
        username="admin",
        email="admin@example.com",
        hashed_password=hash_password("admin_test_123!"),
        role="admin",
        role_id=admin_role.id,
        is_active=True,
    )
    operator = User(
        username="operator",
        email="operator@example.com",
        hashed_password=hash_password("operator_test_123!"),
        role="operator",
        role_id=operator_role.id,
        is_active=True,
    )
    viewer = User(
        username="viewer",
        email="viewer@example.com",
        hashed_password=hash_password("viewer_test_123!"),
        role="viewer",
        role_id=viewer_role.id,
        is_active=True,
    )
    db.add_all([admin, operator, viewer])
    db.commit()
    db.close()

    yield

    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session():
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, client=("127.0.0.1", 51000)) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def admin_headers(db_session):
    user = db_session.query(User).filter(User.username == "admin").first()
    token = create_access_token(data={"sub": user.username, "user_id": user.id, "role": user.role})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def operator_headers(db_session):
    user = db_session.query(User).filter(User.username == "operator").first()
    token = create_access_token(data={"sub": user.username, "user_id": user.id, "role": user.role})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def viewer_headers(db_session):
    user = db_session.query(User).filter(User.username == "viewer").first()
    token = create_access_token(data={"sub": user.username, "user_id": user.id, "role": user.role})
    return {"Authorization": f"Bearer {token}"}
