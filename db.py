import sqlite3
from contextlib import contextmanager
from flask import g

DATABASE = 'database.db'

def get_db():
    """
    현재 HTTP Request 컨텍스트에 할당된 DB Connection을 반환합니다.
    없을 경우 신규 연결을 생성하고 PRAGMA 옵션을 설정합니다.
    """
    if 'db' not in g:
        g.db = sqlite3.connect(
            DATABASE,
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        # 1. 컬럼명 접근 가능 설정 (row['user_id'])
        g.db.row_factory = sqlite3.Row
        
        # 2. 외래키 제약조건 강제 활성화 (SQLite 기본값 OFF 대응)
        g.db.execute("PRAGMA foreign_keys = ON;")
        
    return g.db

def close_db(e=None):
    """
    HTTP Request 처리가 끝날 때 Flask가 자동으로 호출하여 Connection을 닫습니다.
    (Connection Leak 방지)
    """
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_app(app):
    """
    Flask 앱 객체에 teardown 프로세스를 등록합니다.
    """
    app.teardown_appcontext(close_db)

@contextmanager
def transaction_scope():
    """
    동시성 제어가 필요한 CUD(Insert/Update/Delete) 작업 전용 트랜잭션 컨텍스트 매니저.
    'BEGIN IMMEDIATE'를 실행하여 쓰기 잠금(Write Lock)을 획득하고,
    작업 성공 시 COMMIT, 예외 발생 시 ROLLBACK을 자동 수행합니다.
    
    사용 예시:
        with transaction_scope() as db:
            db.execute("INSERT INTO ...")
    """
    db = get_db()
    # 쓰기 잠금 획득 (다른 스레드/프로세스의 쓰기 작업 대기 상태로 전환)
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise