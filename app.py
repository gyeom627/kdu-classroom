from flask import Flask, render_template, request, session, redirect, url_for
import os
import sqlite3
from datetime import datetime
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
# [GitHub 공개 시 수정] 고정 기본 키가 공개되지 않도록, 환경변수가 없으면 실행할 때마다 임시 키를 만든다
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or os.urandom(24)
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# [2차 추가] 파일 업로드 보안성 강화: 악성 파일 업로드 및 대용량 서버 디스크 고갈(DoS) 방지를 위한 최대 업로드 용량(5MB) 제한 설정
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

def allowed_file(filename):
    # [2차 추가] 파일 업로드 보안성 강화: 허용된 이미지 확장자(png, jpg, jpeg, gif) 외의 실행 파일(.exe, .php 등) 업로드 차단 함수
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# 2회차 강의실 대여 프로젝트 전용 데이터베이스 파일 경로 설정
DB_PATH = 'database.db'

def get_db_connection():
    """
    SQLite 데이터베이스 연결 및 공통 설정을 구성하는 함수입니다.
    busy_timeout과 row_factory를 적용하여 안전한 동시성 접근을 지원합니다[cite: 3].
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON;')
    conn.execute('PRAGMA busy_timeout = 10000;')
    return conn

PERIOD_MAP = {
    1: ("09:30", "10:20"),
    2: ("10:30", "11:20"),
    3: ("11:30", "12:20"),
    4: ("12:30", "13:20"),
    5: ("13:30", "14:20"),
    6: ("14:30", "15:20"),
    7: ("15:30", "16:20"),
    8: ("16:30", "17:20"),
    9: ("17:30", "18:20"),
}

# app.py에 추가할 교시 정의 상수
PERIODS = [
    {"period": 1, "start": "09:30", "end": "10:20"},
    {"period": 2, "start": "10:30", "end": "11:20"},
    {"period": 3, "start": "11:30", "end": "12:20"},
    {"period": 4, "start": "12:30", "end": "13:20"},
    {"period": 5, "start": "13:30", "end": "14:20"},
    {"period": 6, "start": "14:30", "end": "15:20"},
    {"period": 7, "start": "15:30", "end": "16:20"},
    {"period": 8, "start": "16:30", "end": "17:20"},
    {"period": 9, "start": "17:30", "end": "18:20"},
]

@app.route('/api/periods')
def api_periods():
    """특정 강의실과 날짜의 교시별 예약/수업/보강 상태를 비동기로 확인하여 반환하는 API 라우트"""
    room_id = request.args.get('room_id', type=int)
    date_str = request.args.get('date')
    head_count = request.args.get('head_count', default=1, type=int)

    if not room_id or not date_str:
        return jsonify({'error': '파라미터가 부족합니다.'}), 400

    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({'error': '날짜 형식이 올바르지 않습니다.'}), 400

    day_of_week = date_obj.weekday()
    conn = get_db_connection()
    try:
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if not room:
            return jsonify({'error': '강의실을 찾을 수 없습니다.'}), 404

        # 해당 날짜의 정규 수업 목록 조회
        schedules = conn.execute(
            """
            SELECT start_time, end_time, course_name FROM class_schedules
            WHERE room_id = ? AND day_of_week = ?
            """,
            (room_id, day_of_week)
        ).fetchall()

        # [3차 추가] 해당 날짜의 보강/특강 목록 조회 <= 초기 버전에는 1회성 보강/특강 일정 조회 기능이 누락되어 있어, 관리자가 등록한 특강 시간대에 학생이 예약하거나 충돌을 방지하기 위해 해당 기능 추가
        supp_classes = conn.execute(
            """
            SELECT start_time, end_time, course_name FROM supplementary_classes
            WHERE room_id = ? AND target_date = ?
            """,
            (room_id, date_str)
        ).fetchall()

        # 해당 날짜의 승인된 예약 목록 조회
        reservations = conn.execute(
            """
            SELECT start_time, end_time, reservation_type, head_count FROM reservations
            WHERE room_id = ? AND date(start_time) = ? AND status = 'APPROVED'
            """,
            (room_id, date_str)
        ).fetchall()

        period_status = []
        for p in PERIODS:
            p_start = p['start']
            p_end = p['end']
            status = 'AVAILABLE'
            reason = '이용 가능'

            # [3차 추가] 0. 보강/특강 충돌 검사 (최우선 차단) <= 초기 버전에는 정규 수업과 일반 예약만 비교하여 관리자 보강일정을 피하지 못하는 문제가 발생하므로 최우선 순위로 보강 충돌 검사 로직 추가
            has_supp = any(sc['start_time'] < p_end and sc['end_time'] > p_start for sc in supp_classes)
            if has_supp:
                status = 'UNAVAILABLE'
                reason = '보강 및 특강 진행 중'
            else:
                # 1. 정규 수업 충돌 검사
                has_class = any(s['start_time'] < p_end and s['end_time'] > p_start for s in schedules)
                if has_class:
                    status = 'UNAVAILABLE'
                    reason = '정규 수업 중'
                else:
                    # 2. 예약 현황 및 정원 검사
                    current_head_sum = 0
                    for r in reservations:
                        r_start = r['start_time'].split(' ')[1][:5]
                        r_end = r['end_time'].split(' ')[1][:5]
                        if r_start < p_end and r_end > p_start:
                            if r['reservation_type'] == 'FULL':
                                current_head_sum = room['capacity']
                                break
                            else:
                                current_head_sum += r['head_count']

                    if current_head_sum >= room['capacity']:
                        status = 'UNAVAILABLE'
                        reason = '예약 마감 (정원 초과)'
                    elif current_head_sum + head_count > room['capacity']:
                        status = 'WARNING'
                        reason = f'잔여 정원 부족 (잔여: {room["capacity"] - current_head_sum}명)'

            period_status.append({
                'period': p['period'],
                'start': p_start,
                'end': p_end,
                'status': status,
                'reason': reason
            })

        return jsonify({'capacity': room['capacity'], 'periods': period_status})
    finally:
        conn.close()

@app.route('/login', methods=['GET', 'POST'])
def login():
    """사용자 및 관리자 로그인 처리를 담당하는 라우트입니다."""
    if request.method == 'POST':
        student_id = (request.form.get('student_id') or '').strip()
        password = (request.form.get('password') or '').strip()

        conn = get_db_connection()
        try:
            user = conn.execute('SELECT * FROM users WHERE student_id = ?', (student_id,)).fetchone()
        finally:
            conn.close()

        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['student_id'] = user['student_id']
            session['name'] = user['name']
            session['role'] = user['role']
            
            if user['role'] == 'ADMIN':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('index'))

        return render_template('login.html', error='학번 또는 비밀번호가 올바르지 않습니다.')

    return render_template('login.html')


@app.route('/logout')
def logout():
    """세션을 초기화하고 로그인 페이지로 이동합니다."""
    session.clear()
    return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """사용자 회원가입 처리 라우트 (비밀번호 단방향 암호화 저장 적용)"""
    if request.method == 'POST':
        student_id = (request.form.get('student_id') or '').strip()
        name = (request.form.get('name') or '').strip()
        password = (request.form.get('password') or '').strip()

        if not student_id or not name or not password:
            return render_template('signup.html', error='모든 항목을 입력해주세요.')

        conn = get_db_connection()
        try:
            existing = conn.execute('SELECT 1 FROM users WHERE student_id = ?', (student_id,)).fetchone()
            if existing:
                return render_template('signup.html', error='이미 가입된 학번입니다.')

            # 비밀번호 해시 생성 및 사용자 등록 (역할은 기본 'STUDENT')
            password_hash = generate_password_hash(password)
            conn.execute(
                'INSERT INTO users (student_id, password_hash, name, role) VALUES (?, ?, ?, ?)',
                (student_id, password_hash, name, 'STUDENT')
            )
            conn.commit()
        finally:
            conn.close()

        return redirect(url_for('login'))

    return render_template('signup.html')

@app.route('/')
def index():
    """메인 예약 대시보드 페이지 (활성 예약 우선 정렬 및 관리자 접근 분기 처리)"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if session.get('role') == 'ADMIN':
        return redirect(url_for('admin_dashboard'))

    conn = get_db_connection()
    try:
        rooms = conn.execute("SELECT * FROM rooms WHERE is_active = 1 ORDER BY room_number").fetchall()
        
        # [1차 추가] 정렬 조건 개선: 초기 버전은 단순 최신순 정렬만 지원하여 지난 예약과 활성 예약이 섞이는 가독성 문제가 있어, 진행 중인 예약(APPROVED 이고 종료 시각 이후)을 최상단(0순위)으로 보여주도록 조건 정렬 쿼리 추가
        my_reservations = conn.execute(
            """
            SELECT r.id, r.reservation_type, r.start_time, r.end_time, r.purpose, r.status, rm.room_number,
                   CASE 
                       WHEN r.status = 'APPROVED' AND r.end_time >= datetime('now', 'localtime') THEN 0 
                       ELSE 1 
                   END AS sort_priority
            FROM reservations r
            JOIN rooms rm ON r.room_id = rm.id
            WHERE r.user_id = ?
            ORDER BY sort_priority ASC, r.start_time DESC
            """,
            (session['user_id'],)
        ).fetchall()
    finally:
        conn.close()

    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return render_template(
        'index.html', 
        rooms=rooms, 
        my_reservations=my_reservations, 
        current_time_str=current_time_str
    )


@app.route('/reserve', methods=['POST'])
def reserve():
    """교시 및 이용 시간 단위 예약 신청 처리 (트랜잭션 격리 및 보강 일정 충돌 차단 방어 로직 포함)"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    try:
        room_id = int(request.form.get('room_id'))
        head_count = int(request.form.get('head_count', 1))
        start_period = int(request.form.get('start_period'))
        duration_hours = int(request.form.get('duration_hours', 1))
    except (TypeError, ValueError):
        return redirect(url_for('index'))

    reservation_date = request.form.get('reservation_date')
    purpose = request.form.get('purpose')

    end_period = start_period + duration_hours - 1
    if start_period not in PERIOD_MAP or end_period not in PERIOD_MAP or end_period > 9:
        return redirect(url_for('index'))

    raw_start_time = PERIOD_MAP[start_period][0] 
    raw_end_time = PERIOD_MAP[end_period][1]     

    start_time = f"{reservation_date} {raw_start_time}:00"
    end_time = f"{reservation_date} {raw_end_time}:00"
    reservation_type = 'FULL' if head_count >= 30 else 'SHARED'

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('BEGIN IMMEDIATE;')

        room = cursor.execute("SELECT * FROM rooms WHERE id = ? AND is_active = 1", (room_id,)).fetchone()
        if not room or head_count > room['capacity']:
            conn.rollback()
            return redirect(url_for('index'))

        # [3차 추가] 해당 날짜 및 시간대에 등록된 보강/특강 일정과 겹치는지 검사 <= 초기 버전에는 학생 예약 시 관리자 특강 일정과의 교차 검증 로직이 없어 특강 중복 예약이 허용되는 결함이 존재하므로 해당 차단 검증 로직 추가
        supp_conflict = cursor.execute(
            """
            SELECT id, course_name FROM supplementary_classes
            WHERE room_id = ? AND target_date = ?
              AND NOT (end_time <= ? OR start_time >= ?)
            """,
            (room_id, reservation_date, raw_start_time, raw_end_time)
        ).fetchone()

        if supp_conflict:
            conn.rollback()
            return f"해당 시간대에는 '{supp_conflict['course_name']}' 보강/특강 일정이 잡혀 있어 예약할 수 없습니다."

        # 예약 등록 완료
        cursor.execute(
            """
            INSERT INTO reservations (user_id, room_id, reservation_type, head_count, purpose, start_time, end_time, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'APPROVED')
            """,
            (session['user_id'], room_id, reservation_type, head_count, purpose, start_time, end_time)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Reservation error: {e}")
    finally:
        conn.close()

    return redirect(url_for('index'))

@app.route('/select_time')
def select_time():
    """선택한 강의실과 날짜의 교시별 상태를 계산하여 버튼형 시간표 예약 단계를 렌더링하는 라우트"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    room_id = request.args.get('room_id', type=int)
    date_str = request.args.get('date')
    head_count = request.args.get('head_count', default=1, type=int)
    purpose = request.args.get('purpose', '')

    if not room_id or not date_str:
        return redirect(url_for('index'))

    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return redirect(url_for('index'))

    now = datetime.now()
    if date_obj < now.date():
        return redirect(url_for('index'))

    today_str = now.strftime("%Y-%m-%d")
    current_time_str = now.strftime("%H:%M")

    day_of_week = date_obj.weekday()
    conn = get_db_connection()
    try:
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if not room:
            return redirect(url_for('index'))

        # 정규 수업 조회
        schedules = conn.execute(
            "SELECT start_time, end_time, course_name FROM class_schedules WHERE room_id = ? AND day_of_week = ?",
            (room_id, day_of_week)
        ).fetchall()

        # [3차 추가] 보강/특강 조회 <= 초기 버전에서는 UI 시간표 조회 시 보강 일정이 반영되지 않아 학생이 예약 가능으로 착각하는 문제를 해결하기 위해 보강 일정 조회 기능 추가
        supp_classes = conn.execute(
            "SELECT start_time, end_time, course_name FROM supplementary_classes WHERE room_id = ? AND target_date = ?",
            (room_id, date_str)
        ).fetchall()

        # 승인된 예약 조회
        reservations = conn.execute(
            "SELECT start_time, end_time, reservation_type, head_count FROM reservations WHERE room_id = ? AND date(start_time) = ? AND status = 'APPROVED'",
            (room_id, date_str)
        ).fetchall()

        period_data = []
        for p in PERIODS:
            p_start = p['start']
            p_end = p['end']
            status = 'AVAILABLE'
            reason = '이용 가능'

            if date_str == today_str and p_start <= current_time_str:
                status = 'UNAVAILABLE'
                reason = '지난 시간'
            else:
                # [3차 추가] 0. 보강/특강 충돌 검사 <= 초기 버전의 시간표 판별 로직에 보강 검사 루프가 누락되어 있어 교시 상태에 반영되도록 추가
                has_supp = any(sc['start_time'] < p_end and sc['end_time'] > p_start for sc in supp_classes)
                if has_supp:
                    status = 'UNAVAILABLE'
                    reason = '보강 및 특강 진행 중'
                else:
                    # 1. 수업 충돌 검사
                    has_class = any(s['start_time'] < p_end and s['end_time'] > p_start for s in schedules)
                    if has_class:
                        status = 'UNAVAILABLE'
                        reason = '정규 수업 중'
                    else:
                        # 2. 예약 인원 및 마감 검사
                        current_head_sum = 0
                        for r in reservations:
                            r_start = r['start_time'].split(' ')[1][:5]
                            r_end = r['end_time'].split(' ')[1][:5]
                            if r_start < p_end and r_end > p_start:
                                if r['reservation_type'] == 'FULL':
                                    current_head_sum = room['capacity']
                                    break
                                else:
                                    current_head_sum += r['head_count']

                        if current_head_sum >= room['capacity']:
                            status = 'UNAVAILABLE'
                            reason = '예약 마감'
                        elif current_head_sum + head_count > room['capacity']:
                            status = 'UNAVAILABLE'
                            reason = f'잔여 정원 부족 (잔여 {room["capacity"] - current_head_sum}명)'

            period_data.append({
                'period': p['period'],
                'start': p_start,
                'end': p_end,
                'status': status,
                'reason': reason
            })

        return render_template(
            'select_time.html', 
            room=room, 
            date_str=date_str, 
            head_count=head_count, 
            purpose=purpose, 
            periods=period_data
        )
    finally:
        conn.close()


@app.route('/confirm_reserve', methods=['POST'])
def confirm_reserve():
    """최종 교시 선택 후 예약 확정 처리 (서버단 동시성 무결성 최종 검증 및 우회 요청 방어)"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    room_id = request.form.get('room_id', type=int)
    reservation_date = request.form.get('date')
    head_count = request.form.get('head_count', type=int)
    purpose = request.form.get('purpose')
    start_period = request.form.get('start_period', type=int)
    duration_hours = request.form.get('duration_hours', type=int, default=1)

    end_period = start_period + duration_hours - 1
    if not start_period or start_period not in PERIOD_MAP or end_period not in PERIOD_MAP or end_period > 9:
        return redirect(url_for('index'))

    try:
        date_obj = datetime.strptime(reservation_date, "%Y-%m-%d").date()
    except ValueError:
        return redirect(url_for('index'))

    now = datetime.now()
    if date_obj < now.date():
        return redirect(url_for('index'))

    raw_start_time = PERIOD_MAP[start_period][0]
    raw_end_time = PERIOD_MAP[end_period][1]

    if reservation_date == now.strftime("%Y-%m-%d") and raw_start_time <= now.strftime("%H:%M"):
        return redirect(url_for('index'))

    start_time = f"{reservation_date} {raw_start_time}:00"
    end_time = f"{reservation_date} {raw_end_time}:00"
    reservation_type = 'FULL' if head_count >= 30 else 'SHARED'

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('BEGIN IMMEDIATE;')
        
        room = cursor.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if not room or head_count > room['capacity']:
            conn.rollback()
            return redirect(url_for('index'))

        # 선택한 시간대의 교시별 잔여 정원 재검증 (우회 요청 방어)
        reservations = cursor.execute(
            "SELECT start_time, end_time, reservation_type, head_count FROM reservations WHERE room_id = ? AND date(start_time) = ? AND status = 'APPROVED'",
            (room_id, reservation_date)
        ).fetchall()

        for p_num in range(start_period, end_period + 1):
            p_start = PERIOD_MAP[p_num][0]
            p_end = PERIOD_MAP[p_num][1]
            
            current_head_sum = 0
            for r in reservations:
                r_start = r['start_time'].split(' ')[1][:5]
                r_end = r['end_time'].split(' ')[1][:5]
                if r_start < p_end and r_end > p_start:
                    if r['reservation_type'] == 'FULL':
                        current_head_sum = room['capacity']
                        break
                    else:
                        current_head_sum += r['head_count']
            
            if current_head_sum + head_count > room['capacity']:
                conn.rollback()
                return redirect(url_for('index'))

        cursor.execute(
            """
            INSERT INTO reservations (user_id, room_id, reservation_type, head_count, purpose, start_time, end_time, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'APPROVED')
            """,
            (session['user_id'], room_id, reservation_type, head_count, purpose, start_time, end_time)
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for('index'))

@app.route('/admin')
def admin_dashboard():
    """관리자 대시보드: 전체 예약 내역, 시설 신고 목록 및 보강 관리 현황 조회 라우트"""
    if 'user_id' not in session or session.get('role') != 'ADMIN':
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        # 전체 예약 내역 조회 (최신순)
        all_reservations = conn.execute(
            """
            SELECT r.id, r.reservation_type, r.start_time, r.end_time, r.purpose, r.status, rm.room_number, u.name, u.student_id
            FROM reservations r
            JOIN rooms rm ON r.room_id = rm.id
            JOIN users u ON r.user_id = u.id
            ORDER BY r.start_time DESC
            """
        ).fetchall()

        # 시설 신고 내역 조회
        reports = conn.execute(
            """
            SELECT rep.id, rep.title, rep.content, rep.status, rep.created_at, rm.room_number, u.name
            FROM reports rep
            JOIN rooms rm ON rep.room_id = rm.id
            JOIN users u ON rep.user_id = u.id
            ORDER BY rep.created_at DESC
            """
        ).fetchall()

        rooms = conn.execute("SELECT id, room_number FROM rooms ORDER BY room_number").fetchall()
        courses = conn.execute("SELECT DISTINCT course_name FROM class_schedules ORDER BY course_name").fetchall()
        
        # [3차 추가] 등록된 보강 목록 조회 <= 관리자가 등록한 보강 및 특강 일정을 대시보드 하단에 리스트로 표기하고 관리(삭제)하기 위한 조회 기능 추가
        supp_classes = conn.execute(
            """
            SELECT sc.id, sc.course_name, sc.target_date, sc.start_time, sc.end_time, rm.room_number
            FROM supplementary_classes sc
            JOIN rooms rm ON sc.room_id = rm.id
            ORDER BY sc.target_date DESC, sc.start_time DESC
            """
        ).fetchall()
    finally:
        conn.close()

    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return render_template('admin.html', all_reservations=all_reservations, reports=reports, rooms=rooms, courses=courses, supp_classes=supp_classes, current_time_str=current_time_str)



@app.route('/cancel/<int:reservation_id>', methods=['POST'])
def cancel(reservation_id):
    """일반 학생의 본인 예약 취소 처리 라우트 (소유권 및 상태 검증 포함)"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('BEGIN IMMEDIATE;')
        res = cursor.execute("SELECT user_id, status FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
        
        if not res or res['status'] == 'CANCELLED':
            conn.rollback()
            return redirect(url_for('index'))

        if session.get('role') != 'ADMIN' and res['user_id'] != session['user_id']:
            conn.rollback()
            return redirect(url_for('index'))

        cursor.execute("UPDATE reservations SET status = 'CANCELLED' WHERE id = ?", (reservation_id,))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for('index'))

@app.route('/admin/cancel/<int:reservation_id>', methods=['POST'])
def admin_cancel_reservation(reservation_id):
    """관리자의 예약 강제 취소 및 취소 사유 인앱 알림 메시지 생성 라우트"""
    if 'user_id' not in session or session.get('role') != 'ADMIN':
        return redirect(url_for('login'))

    cancel_reason = request.form.get('cancel_reason', '관리자에 의한 강제 취소입니다.').strip()

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('BEGIN IMMEDIATE;')
        
        res = conn.execute(
            """
            SELECT r.user_id, r.start_time, rm.room_number 
            FROM reservations r
            JOIN rooms rm ON r.room_id = rm.id
            WHERE r.id = ? AND r.status = 'APPROVED'
            """,
            (reservation_id,)
        ).fetchone()

        if not res:
            conn.rollback()
            return redirect(url_for('admin_dashboard'))

        target_user_id = res['user_id']
        room_num = res['room_number']
        res_time = res['start_time']

        cursor.execute("UPDATE reservations SET status = 'CANCELLED' WHERE id = ?", (reservation_id,))

        # [2차 추가] 인앱 알림 연동: 관리자가 강제 취소할 때 사유를 포함한 알림을 notifications 테이블에 기록하여 학생 로그인 시 알림 팝업 제공
        notification_msg = f"[{room_num}] {res_time[:16]} 예약이 관리자에 의해 취소되었습니다.\n사유: {cancel_reason}"
        cursor.execute(
            """
            INSERT INTO notifications (user_id, message, is_read)
            VALUES (?, ?, 0)
            """,
            (target_user_id, notification_msg)
        )

        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/supplementary/add', methods=['POST'])
def add_supplementary_class():
    """관리자 보강/특강 등록 라우트 (1순위 정규수업 충돌 차단, 3순위 학생예약 자동 취소 및 인앱 알림 연동)"""
    if 'user_id' not in session or session.get('role') != 'ADMIN':
        return redirect(url_for('login'))

    room_id = request.form.get('room_id', type=int)
    course_name = request.form.get('course_name', '').strip()
    target_date = request.form.get('target_date', '').strip()
    
    try:
        start_period = int(request.form.get('start_period'))
        duration_hours = int(request.form.get('duration_hours', 1))
    except (TypeError, ValueError):
        return redirect(url_for('admin_dashboard'))

    end_period = start_period + duration_hours - 1
    if start_period not in PERIOD_MAP or end_period not in PERIOD_MAP or end_period > 9:
        return redirect(url_for('admin_dashboard'))

    start_time = PERIOD_MAP[start_period][0] 
    end_time = PERIOD_MAP[end_period][1]     

    day_of_week = datetime.strptime(target_date, '%Y-%m-%d').weekday()
    full_start = f"{target_date} {start_time}:00"
    full_end = f"{target_date} {end_time}:00"

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('BEGIN IMMEDIATE;')

        # 1순위 검증: 기존 정규 수업과 겹치는지 확인 (정규 수업이 최우선이므로 겹치면 등록 불가)
        regular_conflict = cursor.execute(
            """
            SELECT id, course_name FROM class_schedules
            WHERE room_id = ? AND day_of_week = ?
              AND NOT (end_time <= ? OR start_time >= ?)
            """,
            (room_id, day_of_week, start_time, end_time)
        ).fetchone()

        if regular_conflict:
            cursor.rollback()
            return f"등록 실패: 1순위인 기존 정규 수업('{regular_conflict['course_name']}') 시간과 겹쳐서 보강을 등록할 수 없습니다."

        room_info = cursor.execute("SELECT room_number FROM rooms WHERE id = ?", (room_id,)).fetchone()
        room_number = room_info['room_number'] if room_info else "해당 강의실"

        # [3차 추가] 3순위(학생 예약) 자동 취소 처리: 보강 등록 시 시간이 겹치는 기존 학생 예약들을 조회하여 자동 취소 처리
        conflicting_reservations = cursor.execute(
            """
            SELECT id, user_id FROM reservations
            WHERE room_id = ? AND status = 'APPROVED'
              AND NOT (end_time <= ? OR start_time >= ?)
            """,
            (room_id, full_start, full_end)
        ).fetchall()

        for res in conflicting_reservations:
            cursor.execute(
                """
                UPDATE reservations
                SET status = 'CANCELLED', cancel_reason = '보강 및 특강'
                WHERE id = ?
                """,
                (res['id'],)
            )
            
            # [2차 추가] 인앱 알림 연동: 보강 일정으로 인해 자동 취소된 학생들에게 인앱 알림 메시지 생성 및 저장
            notif_message = f"[{room_number}] {target_date} {start_time}~{end_time} 예약이 보강 및 특강({course_name}) 일정으로 인해 자동 취소되었습니다."
            cursor.execute(
                """
                INSERT INTO notifications (user_id, message)
                VALUES (?, ?)
                """,
                (res['user_id'], notif_message)
            )

        # 보강 일정 등록
        cursor.execute(
            """
            INSERT INTO supplementary_classes (room_id, course_name, target_date, start_time, end_time)
            VALUES (?, ?, ?, ?, ?)
            """,
            (room_id, course_name, target_date, start_time, end_time)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error adding supplementary class: {e}")
    finally:
        conn.close()

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/schedule/delete/supp/<int:item_id>', methods=['POST'])
def delete_supplementary_class(item_id):
    """[3차 추가] 관리자 보강/특강 일정 삭제 라우트 <= 등록된 보강 일정을 취소/삭제할 수 있는 관리 기능 추가"""
    if 'user_id' not in session or session.get('role') != 'ADMIN':
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM supplementary_classes WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()

    return redirect(url_for('admin_dashboard'))


@app.route('/past_reservations')
def past_reservations():
    """지나간 예약 및 취소된 예약 내역 목록을 모아보는 전용 페이지 라우트"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        past_list = conn.execute(
            """
            SELECT r.id, r.reservation_type, r.start_time, r.end_time, r.purpose, r.status, rm.room_number,
                   CASE 
                       WHEN r.status = 'APPROVED' AND r.end_time >= datetime('now', 'localtime') THEN 0 
                       ELSE 1 
                   END AS sort_priority
            FROM reservations r
            JOIN rooms rm ON r.room_id = rm.id
            WHERE r.user_id = ? AND (r.status = 'CANCELLED' OR r.end_time < datetime('now', 'localtime'))
            ORDER BY r.start_time DESC
            """,
            (session['user_id'],)
        ).fetchall()
    finally:
        conn.close()

    return render_template('past_reservations.html', past_list=past_list)


@app.route('/report/create', methods=['POST'])
def create_report():
    """시설 애로사항 신고 등록 라우트 ([2차 추가] 파일 업로드 및 보안 검증 기능 포함)"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    reservation_id = request.form.get('reservation_id', type=int)
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()

    if not reservation_id or not title or not content:
        return redirect(url_for('reports_page'))

    # [2차 추가] 파일 업로드 처리: secure_filename과 allowed_file 검증을 통해 안전하게 서버 저장 폴더에 저장
    image_path = None
    file = request.files.get('report_image')
    if file and file.filename != '':
        if not allowed_file(file.filename):
            # 허용되지 않은 파일 형식일 경우 차단
            return "이미지 파일(png, jpg, jpeg)만 업로드할 수 있습니다.", 400
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(save_path)
        image_path = f"/{save_path}"

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('BEGIN IMMEDIATE;')
        res = cursor.execute(
            "SELECT room_id, user_id FROM reservations WHERE id = ? AND user_id = ?",
            (reservation_id, session['user_id'])
        ).fetchone()

        if not res:
            conn.rollback()
            return redirect(url_for('reports_page'))

        cursor.execute(
            """
            INSERT INTO reports (reservation_id, user_id, room_id, title, content, status)
            VALUES (?, ?, ?, ?, ?, 'PENDING')
            """,
            (reservation_id, session['user_id'], res['room_id'], title, content)
        )
        report_id = cursor.lastrowid

        if image_path:
            cursor.execute(
                "INSERT INTO report_images (report_id, image_path) VALUES (?, ?)",
                (report_id, image_path)
            )

        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for('reports_page'))


@app.route('/report/<int:report_id>')
def report_detail(report_id):
    """신고 상세 내용, 첨부 사진 및 관리자 답변을 확인하는 페이지 라우트 (권한 검증 포함)"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    role = session.get('role')

    conn = get_db_connection()
    try:
        if role == 'ADMIN':
            report = conn.execute(
                """
                SELECT rep.*, rm.room_number, u.name, u.student_id
                FROM reports rep
                JOIN rooms rm ON rep.room_id = rm.id
                JOIN users u ON rep.user_id = u.id
                WHERE rep.id = ?
                """,
                (report_id,)
            ).fetchone()
        else:
            report = conn.execute(
                """
                SELECT rep.*, rm.room_number, u.name, u.student_id
                FROM reports rep
                JOIN rooms rm ON rep.room_id = rm.id
                JOIN users u ON rep.user_id = u.id
                WHERE rep.id = ? AND rep.user_id = ?
                """,
                (report_id, user_id)
            ).fetchone()

        if not report:
            return redirect(url_for('index'))

        images = conn.execute(
            "SELECT image_path FROM report_images WHERE report_id = ?",
            (report_id,)
        ).fetchall()
    finally:
        conn.close()

    return render_template('report_detail.html', report=report, images=images)


@app.route('/report/reply/<int:report_id>', methods=['POST'])
def reply_report(report_id):
    """관리자의 시설 신고 답변 등록 및 처리 상태 변경 라우트"""
    if 'user_id' not in session or session.get('role') != 'ADMIN':
        return redirect(url_for('login'))

    admin_reply = request.form.get('admin_reply', '').strip()
    status = request.form.get('status', 'PENDING')

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE reports 
            SET admin_reply = ?, status = ? 
            WHERE id = ?
            """,
            (admin_reply, status, report_id)
        )
        conn.commit()
    finally:
        conn.close()

    if status == 'RESOLVED':
        return redirect(url_for('admin_dashboard'))
    else:
        return redirect(url_for('report_detail', report_id=report_id))


@app.route('/reports')
def reports_page():
    """학생 본인의 시설 신고 내역 조회 및 작성 폼 페이지 라우트 ([1차 추가] 1예약당 1회 신고 제한 제약 반영)"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if session.get('role') == 'ADMIN':
        return redirect(url_for('admin_dashboard'))

    conn = get_db_connection()
    try:
        # [1차 추가] 도배 방지 및 무결성 강화: 이미 신고가 접수된 이용 완료 예약 건은 신고 대상 목록에서 제외하여 중복 신고 방지
        my_finished_reservations = conn.execute(
            """
            SELECT r.id, r.start_time, rm.room_number 
            FROM reservations r
            JOIN rooms rm ON r.room_id = rm.id
            WHERE r.user_id = ? AND r.status = 'APPROVED' AND r.end_time < datetime('now', 'localtime')
              AND r.id NOT IN (SELECT reservation_id FROM reports)
            ORDER BY r.start_time DESC
            """,
            (session['user_id'],)
        ).fetchall()

        reports = conn.execute(
            """
            SELECT rep.id, rep.title, rep.content, rep.status, rep.created_at, rm.room_number
            FROM reports rep
            JOIN rooms rm ON rep.room_id = rm.id
            WHERE rep.user_id = ?
            ORDER BY rep.created_at DESC
            """,
            (session['user_id'],)
        ).fetchall()
    finally:
        conn.close()

    return render_template('reports.html', reports=reports, my_finished_reservations=my_finished_reservations)


@app.route('/notifications_mail')
def notifications_mail():
    """[2차 추가] 지난 인앱 알림 내역을 모아보는 알림 메일함 페이지 라우트"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        notifications = conn.execute(
            """
            SELECT id, message, is_read, created_at FROM notifications
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (session['user_id'],)
        ).fetchall()
    finally:
        conn.close()

    return render_template('notifications_mail.html', notifications=notifications)

@app.route('/notification/read/<int:notif_id>', methods=['POST', 'GET'])
def read_notification(notif_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = sqlite3.connect(DB_PATH)
    # 알림 테이블 이름과 컬럼명에 맞춰 수정하세요 (예: notifications 테이블의 is_read를 1로 변경)
    conn.execute("UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?", 
                 (notif_id, session['user_id']))
    conn.commit()
    conn.close()
    
    # 처리 후 알림 메일함으로 다시 리다이렉트
    return redirect(url_for('notifications_mail')) # 또는 알림 페이지 함수명

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)