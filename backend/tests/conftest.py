from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_user
from app.db.models import Base, User
from app.db.session import get_db
from app.main import app


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    with TestingSession() as db:
        yield db


def _first_user() -> User:
    # Mirrors the pre-login single-user behaviour so API tests can focus on their own feature.
    # Tests that don't swap in a test database get a stand-in user instead of touching Postgres.
    db_override = app.dependency_overrides.get(get_db)
    if db_override is None:
        return User(id=1, email="test-user@example.invalid", is_admin=True)
    db_gen = db_override()
    db = next(db_gen)
    try:
        user = db.scalar(select(User).order_by(User.id))
        if user is None:
            user = User(email="test-user@example.invalid")
            db.add(user)
            db.commit()
            db.refresh(user)
        # Act as the owner so tests of owner-only actions (deleting jobs/sources) keep working.
        user.is_admin = True
        return user
    finally:
        db_gen.close()


@pytest.fixture(autouse=True)
def signed_in_as_first_user(request) -> Generator[None, None, None]:
    if request.node.get_closest_marker("real_auth"):
        yield
        return
    app.dependency_overrides[get_current_user] = _first_user
    yield
    app.dependency_overrides.pop(get_current_user, None)
