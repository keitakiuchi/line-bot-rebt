import os
import psycopg2

# データベース接続情報を環境変数から取得
DATABASE_URL = os.environ['DATABASE_URL']

def create_tables():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS line_bot_logs (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP,
                sender VARCHAR(255),
                lineId VARCHAR(255),
                stripeId VARCHAR(255),
                message TEXT,
                is_active BOOLEAN,
                sys_prompt TEXT
            )
        """)
        conn.commit()
        print("Table created successfully")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    create_tables()
