-- 외래 키 활성화 (SQLite 필수)
PRAGMA foreign_keys = ON;

-- 1. 사용자 테이블 (학생 / 관리자)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT UNIQUE NOT NULL,      -- 학번 또는 관리자 ID
    password_hash TEXT NOT NULL,          -- 비밀번호 (Werkzeug pbkdf2:sha256)
    name TEXT NOT NULL,                   -- 이름
    role TEXT CHECK(role IN ('STUDENT', 'ADMIN')) DEFAULT 'STUDENT',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. 강의실 테이블
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_number TEXT UNIQUE NOT NULL,      -- 호수 및 명칭 (예: '1809호 (오픈소스SW실습실)')
    floor INTEGER NOT NULL,                -- 층수 (예: 8)
    capacity INTEGER NOT NULL CHECK(capacity > 0), -- 수용 정원 (예: 40)
    is_active BOOLEAN DEFAULT 1
);

-- 3. 정규 수업 및 보강 시간표 테이블 (자동 취소용)
CREATE TABLE IF NOT EXISTS class_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    course_name TEXT NOT NULL,            -- 과목명
    day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6), -- 0: 월요일 ~ 6: 일요일
    start_time TEXT NOT NULL,             -- 'HH:MM' (예: '09:00')
    end_time TEXT NOT NULL,               -- 'HH:MM' (예: '10:30')
    is_supplementary BOOLEAN DEFAULT 0,   -- [1차 추가] 초기 프로토타입 구상 시 정규 수업 테이블에 보강 필드를 통합하려 했으나, 일회성 보강/특강 일정을 유연하게 관리하기에 구조적 한계가 있어 해당 필드는 폐기하고 별도 테이블로 분리함
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
);

-- [3차 추가] 보강 및 특강 일정 테이블 <= 기존 정규 수업과 달리 특정 날짜에만 발생하는 일회성 보강/특강을 관리하고 학생 예약 충돌 및 자동 취소를 처리하기 위해 신규 추가
CREATE TABLE IF NOT EXISTS supplementary_classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    course_name TEXT NOT NULL,
    target_date TEXT NOT NULL, -- 형식: YYYY-MM-DD
    start_time TEXT NOT NULL,  -- 형식: HH:MM
    end_time TEXT NOT NULL,    -- 형식: HH:MM
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
);

-- 4. 예약 테이블
CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    room_id INTEGER NOT NULL,
    reservation_type TEXT CHECK(reservation_type IN ('FULL', 'SHARED')) NOT NULL, -- 전체 / 소수
    head_count INTEGER NOT NULL CHECK(head_count > 0),                             -- 신청 인원
    purpose TEXT NOT NULL,                                                         -- 사용 목적
    start_time DATETIME NOT NULL,                                                  -- YYYY-MM-DD HH:MM:SS
    end_time DATETIME NOT NULL,                                                    -- YYYY-MM-DD HH:MM:SS
    status TEXT CHECK(status IN ('APPROVED', 'CANCELLED')) DEFAULT 'APPROVED',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
);

-- 5. 시설 애로사항 신고 테이블 ([1차 추가] DoS 공격 및 무분별한 도배 방지를 위해 1개 예약당 단 1회의 신고만 허용하는 UNIQUE 제약 추가)
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER UNIQUE NOT NULL, 
    user_id INTEGER NOT NULL,
    room_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    admin_reply TEXT,
    status TEXT CHECK(status IN ('PENDING', 'RESOLVED')) DEFAULT 'PENDING',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (reservation_id) REFERENCES reservations(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
);

-- 6. 신고 이미지 테이블 ([2차 추가] 파일 업로드 기능 구현 시 하나의 신고 건에 다중 현장 사진을 첨부할 수 있도록 지원하기 위해 추가된 1:N 관계 테이블)
CREATE TABLE IF NOT EXISTS report_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    image_path TEXT NOT NULL,             -- 서버 내 저장 파일 경로
    uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);

-- 7. 인앱 알림 테이블 ([2차 추가] 관리자 강제 취소 및 보강에 따른 자동 취소 시 사용자에게 사유를 능동적으로 전달하기 위해 이메일 외부 연동 대신 구현한 인앱 알림함 테이블)
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 인덱스 추가 ([1차 추가] 대용량 예약 조회 및 요일별 수업 시간표 검색 시 동시성 성능 최적화와 병목 현상 방지를 위해 추가된 인덱스)
CREATE INDEX IF NOT EXISTS idx_reservations_room_time ON reservations (room_id, start_time, end_time, status);
CREATE INDEX IF NOT EXISTS idx_schedules_room_day ON class_schedules (room_id, day_of_week);