from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# База будет сохраняться в файл schedule.db в корне проекта
SQLALCHEMY_DATABASE_URL = "sqlite:///./schedule.db"

# check_same_thread=False нужно только для SQLite в FastAPI
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Зависимость для получения сессии БД в эндпоинтах
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
