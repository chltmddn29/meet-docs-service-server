import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Render는 DATABASE_URL 환경변수를 줌. 없으면 로컬 SQLite 사용
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./meetdocs.db")

# Render의 postgres:// → postgresql:// 변환 (SQLAlchemy 요구사항)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLite일 때만 connect_args 필요
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()