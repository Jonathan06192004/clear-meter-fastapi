from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os
from dotenv import load_dotenv

load_dotenv()

# Use your exact PostgreSQL URL from Render
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://aquameter_user:q0JSnRKWQlpJrgHlKostKPTOXN9Rz0xp@dpg-d3ht4abuibrs73b6qkgg-a.singapore-postgres.render.com/aquameter"
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
