"""板块黑名单持久化。"""

from dragon_quant.storage._base import _connect, _ensure_schema, _lock


def get_sector_blacklist() -> list[str]:
    """返回板块黑名单名称列表（拉取领涨/领跌板块时按此过滤）。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            rows = conn.execute("SELECT name FROM sector_blacklist").fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()


def add_sector_blacklist(name: str):
    """新增一个黑名单概念（幂等）。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT OR IGNORE INTO sector_blacklist(name) VALUES (?)",
                (name.strip(),))
            conn.commit()
        finally:
            conn.close()


def remove_sector_blacklist(name: str):
    """移除一个黑名单概念。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute("DELETE FROM sector_blacklist WHERE name = ?",
                         (name.strip(),))
            conn.commit()
        finally:
            conn.close()

