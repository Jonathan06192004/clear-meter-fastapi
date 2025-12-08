from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os
from dotenv import load_dotenv

load_dotenv()

# Use your exact PostgreSQL URL from Render
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://aquameter_user:egaiwPMT5bDfW5eyvFhe2j9du7NSfV3j@dpg-d4qfu6chg0os73894hb0-a.singapore-postgres.render.com/aquameter_68ce"
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
