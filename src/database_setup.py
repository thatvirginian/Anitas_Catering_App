# -*- coding: utf-8 -*-
import os
from sqlalchemy import create_engine, text, Column, String, Float, DateTime, func
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

# 1. Environment & Base Configuration
load_dotenv(override=True)
Base = declarative_base()

# 2. Construct Connection URL
connection_url = URL.create(
    drivername="postgresql+psycopg2",
    username=os.getenv("PGUSER"),
    password=os.getenv("PGPASSWORD"),
    host=os.getenv("PGHOST"),
    port=os.getenv("PGPORT"),
    database=os.getenv("PGDATABASE"),
    query={"sslmode": "require"},
)

# 3. Create the Engine
engine = create_engine(
    connection_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

# 4. Session Factory
# This is what allows ToastAPI() to "grab its own key" to the database
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# 5. Token Model (The "Filing Cabinet" Slot)
class APIToken(Base):
    __tablename__ = 'api_tokens'
    service_name = Column(String, primary_key=True)
    access_token = Column(String, nullable=False)
    client_id = Column(String, nullable=False)
    expires_at = Column(Float, nullable=False)
    created_at = Column(Float, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


def get_engine():
    """Returns the SQLAlchemy engine for orders_pull_Update.py."""
    return engine


