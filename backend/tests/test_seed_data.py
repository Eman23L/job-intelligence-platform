from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, ExcludedTechnology, TargetRole, User, UserSkill
from scripts.seed_data import EXCLUDED_TECHNOLOGIES, SKILLS, TARGET_ROLES, seed_database


def test_seed_database_loads_profile_data() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as db:
        user = seed_database(db, email="test@example.com")

        assert user.email == "test@example.com"
        assert db.scalar(select(User).where(User.email == "test@example.com")) is not None
        assert len(db.scalars(select(UserSkill)).all()) == len(SKILLS)
        assert len(db.scalars(select(TargetRole)).all()) == len(TARGET_ROLES)
        assert len(db.scalars(select(ExcludedTechnology)).all()) == len(EXCLUDED_TECHNOLOGIES)


def test_seed_database_is_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as db:
        seed_database(db, email="test@example.com")
        seed_database(db, email="test@example.com")

        assert len(db.scalars(select(User)).all()) == 1
        assert len(db.scalars(select(UserSkill)).all()) == len(SKILLS)
        assert len(db.scalars(select(TargetRole)).all()) == len(TARGET_ROLES)
        assert len(db.scalars(select(ExcludedTechnology)).all()) == len(EXCLUDED_TECHNOLOGIES)
