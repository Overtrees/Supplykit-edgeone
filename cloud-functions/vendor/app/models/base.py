"""SQLAlchemy 基础配置：引擎、会话、Base"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declared_attr, declarative_base
import os

DB_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(os.path.dirname(__file__),'..','..','supplykit.db')}")

engine = None
SessionLocal = None

def init_engine(url=None):
    global engine, SessionLocal
    engine = create_engine(url or DB_URL, echo=False, connect_args={"check_same_thread": False} if "sqlite" in (url or DB_URL) else {})
    SessionLocal = sessionmaker(bind=engine)

def get_session():
    if SessionLocal is None:
        init_engine()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class CustomBase:
    @declared_attr
    def __tablename__(cls):
        return cls.__name__.lower() + "s" if not cls.__name__.endswith("s") else cls.__name__.lower()

    id = None  # each model defines its own id

Base = declarative_base(cls=CustomBase)
