import pymysql
from sshtunnel import SSHTunnelForwarder

from .config import (
    MYSQL_HOST,
    MYSQL_PASSWORD,
    MYSQL_PORT,
    MYSQL_USER,
    SSH_HOST,
    SSH_PASSWORD,
    SSH_PORT,
    SSH_USER,
)


def get_experiment_counts(database: str, table: str) -> dict:
    with SSHTunnelForwarder(
        (SSH_HOST, SSH_PORT),
        ssh_username=SSH_USER,
        ssh_password=SSH_PASSWORD,
        remote_bind_address=(MYSQL_HOST, MYSQL_PORT),
    ) as tunnel:
        conn = pymysql.connect(
            host="127.0.0.1",
            port=tunnel.local_bind_port,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=database,
            connect_timeout=10,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                total = cur.fetchone()[0]
                cur.execute(f"SELECT COUNT(*) FROM `{table}` WHERE training_termination_reason = 'completed'")
                complete = cur.fetchone()[0]
        finally:
            conn.close()
    return {"total": total, "complete": complete}
