from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job


def delete_job(db: Session, job: Job) -> int:
    db.delete(job)
    db.commit()
    return 1


def delete_jobs(db: Session, job_ids: list[int]) -> list[int]:
    jobs = db.scalars(select(Job).where(Job.id.in_(job_ids))).all()
    deleted_ids = [job.id for job in jobs]
    for job in jobs:
        db.delete(job)
    db.commit()
    return deleted_ids


def exclude_jobs(db: Session, job_ids: list[int]) -> list[int]:
    jobs = db.scalars(select(Job).where(Job.id.in_(job_ids))).all()
    excluded_ids: list[int] = []
    for job in jobs:
        job.status = "excluded"
        excluded_ids.append(job.id)
    db.commit()
    return excluded_ids
