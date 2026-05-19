import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExcludedTechnology, TargetRole, User, UserSkill
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

DEFAULT_USER_EMAIL = "job-search-user@example.com"

SKILLS = [
    "Python",
    "SQL",
    "JavaScript",
    "TypeScript",
    "React",
    "Next.js",
    "data modelling",
    "dashboard reporting",
    "process automation",
    "systems integration",
    "cloud infrastructure",
    "Linux/WSL",
    "Git",
    "BeautifulSoup",
    "Requests",
    "JSON",
    "Supabase",
]

TARGET_ROLES = [
    "Data Engineer",
    "Python Data Engineer",
    "Data Platform Engineer",
    "Analytics Engineer",
    "Data & Automation Engineer",
    "Workflow Automation Engineer",
    "Process Automation Engineer",
    "Internal Tools Engineer",
    "Full Stack Automation Engineer",
    "AI Automation Engineer",
    "Technical Consultant Data Automation",
    "Digital Transformation Consultant",
]

EXCLUDED_TECHNOLOGIES = [
    "Power Platform",
    "Power Apps",
    "Power Automate",
    "Power BI",
    "Dataverse",
    "Power Fx",
    "DAX",
    "Power Query",
    "Copilot Studio",
    "AI Builder",
]


def get_or_create_user(db: Session, email: str = DEFAULT_USER_EMAIL) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        return user

    user = User(email=email)
    db.add(user)
    db.flush()
    return user


def seed_user_skills(db: Session, user: User) -> None:
    existing = set(
        db.scalars(select(UserSkill.skill_name).where(UserSkill.user_id == user.id)).all()
    )
    for skill in SKILLS:
        if skill not in existing:
            db.add(
                UserSkill(
                    user_id=user.id,
                    skill_name=skill,
                    category=None,
                    proficiency=None,
                    years_experience=None,
                    is_primary=True,
                )
            )


def seed_target_roles(db: Session, user: User) -> None:
    existing = set(
        db.scalars(select(TargetRole.role_title).where(TargetRole.user_id == user.id)).all()
    )
    for priority, role_title in enumerate(TARGET_ROLES, start=1):
        if role_title not in existing:
            db.add(TargetRole(user_id=user.id, role_title=role_title, priority=priority))


def seed_excluded_technologies(db: Session) -> None:
    existing = set(db.scalars(select(ExcludedTechnology.name)).all())
    reason = "Excluded by user preference for this job-search platform."
    for name in EXCLUDED_TECHNOLOGIES:
        if name not in existing:
            db.add(ExcludedTechnology(name=name, reason=reason))


def seed_database(db: Session, email: str = DEFAULT_USER_EMAIL) -> User:
    user = get_or_create_user(db, email=email)
    seed_user_skills(db, user)
    seed_target_roles(db, user)
    seed_excluded_technologies(db)
    db.commit()
    db.refresh(user)
    return user


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    with SessionLocal() as db:
        user = seed_database(db)
    logger.info("Seed data loaded for user %s", user.email)


if __name__ == "__main__":
    main()
