import os
from dotenv import load_dotenv

# Переменные из .env
load_dotenv()

print("DEBUG DB_HOST =", os.getenv("DB_HOST"))


config = {
    "TOKEN": os.getenv("TOKEN"),
    "DB": {
        "host": os.getenv("DB_HOST"),
        "port": int(os.getenv("DB_PORT", 5432)),
        "name": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "schema": os.getenv("DB_SCHEMA", "public"),
    }
}
