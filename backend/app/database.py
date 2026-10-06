from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

# Рабочая база подключена к файлу, который пробрасывает Docker Compose.
SQLALCHEMY_DATABASE_URL = "sqlite:///./schedule.db"

# check_same_thread=False нужно только для SQLite в FastAPI
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)


@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, _):
    """Make SQLite enforce the relationships declared by the models."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Зависимость для получения сессии БД в эндпоинтах
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
