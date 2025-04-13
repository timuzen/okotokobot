# db_ping.py
import asyncpg
from config import config

async def check_db():
    try:
        db = config["DB"]
        conn = await asyncpg.connect(
            host=db["host"],
            port=db["port"],
            user=db["user"],
            password=db["password"],
            database=db["name"]
        )
        await conn.execute(f"SET search_path TO {db['schema']}")
        await conn.execute("SELECT 1")
        await conn.close()
        return True
    except Exception as e:
        print(f"❌ DB check failed: {e}")
        return False
