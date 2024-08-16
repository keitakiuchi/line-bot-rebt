import os
import psycopg2

DATABASE_URL = os.environ['DATABASE_URL']

def table_exists(cur, table_name):
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = %s
        );
    """, (table_name,))
    return cur.fetchone()[0]

def create_tables():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cur = conn.cursor()
    try:
        if not table_exists(cur, 'line_bot_logs'):
            cur.execute("""
                CREATE TABLE line_bot_logs (
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
            print("Table 'line_bot_logs' created successfully")
        else:
            print("Table 'line_bot_logs' already exists")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    create_tables()
    
