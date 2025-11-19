from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3
from datetime import datetime, timedelta
import csv
import os
import threading
import time
from functools import wraps

app = Flask(__name__, template_folder='ui', static_folder='ui')
CORS(app)

# Database setup
DATABASE = 'bookings.db'

# Allowed email IDs (13 faculty + 1 HOD)
ALLOWED_EMAILS = {
    'deveshsir@geu.ac.in',  # HOD
    '2000001@geu.ac.in',    # Prof. Gupta
    '2000002@geu.ac.in',    # Dr. Iyer
    '2000003@geu.ac.in',    # Prof. Joshi
    '2000004@geu.ac.in',    # Dr. Khan
    '2000005@geu.ac.in',    # Prof. Kapoor
    '2000006@geu.ac.in',    # Prof. Kaur
    '2000007@geu.ac.in',    # Dr. Mehta
    '2000008@geu.ac.in',    # Prof. Nair
    '2000009@geu.ac.in',    # Prof. Patel
    '2000010@geu.ac.in',    # Dr. Rao
    '2000011@geu.ac.in',    # Prof. Reddy
    '2000012@geu.ac.in',    # Dr. Sharma
    '2000013@geu.ac.in'     # Prof. Singh
}

# Faculty name mapping
FACULTY_NAMES = {
    '2000001@geu.ac.in': 'Prof. Gupta',
    '2000002@geu.ac.in': 'Dr. Iyer',
    '2000003@geu.ac.in': 'Prof. Joshi',
    '2000004@geu.ac.in': 'Dr. Khan',
    '2000005@geu.ac.in': 'Prof. Kapoor',
    '2000006@geu.ac.in': 'Prof. Kaur',
    '2000007@geu.ac.in': 'Dr. Mehta',
    '2000008@geu.ac.in': 'Prof. Nair',
    '2000009@geu.ac.in': 'Prof. Patel',
    '2000010@geu.ac.in': 'Dr. Rao',
    '2000011@geu.ac.in': 'Prof. Reddy',
    '2000012@geu.ac.in': 'Dr. Sharma',
    '2000013@geu.ac.in': 'Prof. Singh'
}

# ============================================================
# OS CONCEPTS IMPLEMENTATION
# ============================================================

# 1. SEMAPHORE - Limit concurrent booking requests (Resource Management)
booking_semaphore = threading.Semaphore(5)  # Max 5 concurrent bookings

# 2. MUTEX LOCKS - Ensure mutual exclusion for critical sections
db_lock = threading.Lock()  # Database access lock
room_locks = {}  # Per-room locks for fine-grained locking
room_locks_mutex = threading.Lock()  # Lock for room_locks dictionary

# 3. READER-WRITER LOCK Implementation
class ReaderWriterLock:
    def __init__(self):
        self.readers = 0
        self.writers = 0
        self.read_ready = threading.Condition(threading.Lock())
        self.write_ready = threading.Condition(threading.Lock())
    
    def acquire_read(self):
        self.read_ready.acquire()
        while self.writers > 0:
            self.read_ready.wait()
        self.readers += 1
        self.read_ready.release()
    
    def release_read(self):
        self.read_ready.acquire()
        self.readers -= 1
        if self.readers == 0:
            self.read_ready.notify_all()
        self.read_ready.release()
    
    def acquire_write(self):
        self.write_ready.acquire()
        while self.writers > 0 or self.readers > 0:
            self.write_ready.wait()
        self.writers += 1
        self.write_ready.release()
    
    def release_write(self):
        self.write_ready.acquire()
        self.writers -= 1
        self.write_ready.notify_all()
        self.read_ready.acquire()
        self.read_ready.notify_all()
        self.read_ready.release()
        self.write_ready.release()

timetable_rw_lock = ReaderWriterLock()

# 4. DEADLOCK PREVENTION - Resource Ordering
def get_room_lock(room_id):
    """Get or create lock for specific room (ordered locking to prevent deadlock)"""
    with room_locks_mutex:
        if room_id not in room_locks:
            room_locks[room_id] = threading.Lock()
        return room_locks[room_id]

# 5. PRIORITY SCHEDULING with AGING
PRIORITY_LEVELS = {
    'urgent': 1,
    'high': 2,
    'normal': 3,
    'low': 4
}

def calculate_priority_with_aging(base_priority, created_at):
    """Implement aging to prevent starvation"""
    age_hours = (datetime.now() - datetime.fromisoformat(created_at)).total_seconds() / 3600
    # Decrease priority value (increase importance) by 1 for every 24 hours waiting
    aging_bonus = int(age_hours / 24)
    final_priority = max(1, base_priority - aging_bonus)
    return final_priority

# 6. CONCURRENCY CONTROL - Transaction decorator
def with_db_transaction(func):
    """Decorator for database transactions with exclusive locking"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        conn = sqlite3.connect(DATABASE, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('BEGIN EXCLUSIVE')  # Exclusive lock prevents race conditions
            result = func(conn, *args, **kwargs)
            conn.commit()
            return result
        except sqlite3.OperationalError as e:
            conn.rollback()
            print(f"Database locked, retrying... {str(e)}")
            time.sleep(0.1)
            return wrapper(*args, **kwargs)  # Retry on lock
        except Exception as e:
            conn.rollback()
            print(f"Transaction error: {str(e)}")
            raise
        finally:
            conn.close()
    return wrapper

def init_db():
    """Initialize SQLite database"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT,
            role TEXT NOT NULL,
            name TEXT,
            first_login INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )
    ''')
    
    # Create timetable table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS timetable (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            time TEXT NOT NULL,
            room TEXT NOT NULL,
            faculty TEXT NOT NULL,
            subject TEXT NOT NULL,
            semester TEXT NOT NULL
        )
    ''')
    
    # Create bookings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty TEXT NOT NULL,
            faculty_name TEXT,
            room TEXT NOT NULL,
            subject TEXT,
            purpose TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            notes TEXT,
            status TEXT DEFAULT 'pending',
            priority TEXT DEFAULT 'normal',
            timestamp TEXT NOT NULL,
            approved_at TEXT,
            rejected_at TEXT
        )
    ''')
    
    # Insert default HOD user if not exists
    cursor.execute('SELECT COUNT(*) FROM users WHERE email = ?', ('deveshsir@geu.ac.in',))
    if cursor.fetchone()[0] == 0:
        cursor.execute('''
            INSERT INTO users (email, password, role, name, first_login, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', ('deveshsir@geu.ac.in', 'qwerty123', 'hod', 'Dr. Devesh', 0, datetime.now().isoformat()))
        print("✅ Default HOD user created: deveshsir@geu.ac.in")
    
    # Pre-populate faculty accounts
    for email, name in FACULTY_NAMES.items():
        cursor.execute('SELECT COUNT(*) FROM users WHERE email = ?', (email,))
        if cursor.fetchone()[0] == 0:
            cursor.execute('''
                INSERT INTO users (email, password, role, name, first_login, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (email, None, 'faculty', name, 1, datetime.now().isoformat()))
    
    conn.commit()
    conn.close()

def load_timetable_from_csv():
    """Load timetable from CSV file into database"""
    csv_path = 'data/timetable.csv'
    
    if not os.path.exists(csv_path):
        print(f"Warning: {csv_path} not found")
        return
    
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # Clear existing timetable
    cursor.execute('DELETE FROM timetable')
    
    # Load from CSV
    with open(csv_path, 'r', encoding='utf-8') as file:
        csv_reader = csv.DictReader(file)
        for row in csv_reader:
            # Convert 12-hour time format to 24-hour format
            time_str = row.get('Time', '')
            if time_str:
                # Handle formats like "01:00-02:00" -> "13:00-14:00"
                parts = time_str.split('-')
                if len(parts) == 2:
                    start, end = parts
                    start_hour = int(start.split(':')[0])
                    end_hour = int(end.split(':')[0])
                    
                    # Convert PM times (01:00-05:00 PM are 13:00-17:00)
                    if start_hour >= 1 and start_hour <= 5 and 'PM' not in time_str:
                        # Check if it's afternoon time (after 12:00)
                        if start_hour < 8:  # Times like 01:00, 02:00 are afternoon
                            start_hour += 12
                            end_hour += 12
                    
                    time_str = f"{start_hour:02d}:{start.split(':')[1]}-{end_hour:02d}:{end.split(':')[1]}"
            
            cursor.execute('''
                INSERT INTO timetable (day, time, room, faculty, subject, semester)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                row.get('Day', ''),
                time_str,
                row.get('Room', ''),
                row.get('Faculty', ''),
                row.get('Subject', ''),
                row.get('Semester', '')
            ))
    
    conn.commit()
    count = cursor.execute('SELECT COUNT(*) FROM timetable').fetchone()[0]
    conn.close()
    
    print(f"✅ Timetable loaded successfully ({count} entries)")

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


@app.route('/')
def index():
    """Serve frontend HTML"""
    return render_template('frontend.html')

@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static files (images, etc.)"""
    return send_from_directory('ui', filename)

@app.route('/api/login', methods=['POST'])
def login():
    """Validate user login with strict email validation"""
    try:
        data = request.json
        email = data.get('email', '').strip()
        password = data.get('password', '')
        role = data.get('role', '')
        
        if not email or not password:
            return jsonify({
                'success': False,
                'message': 'Email and password are required'
            }), 400
        
        # Check if email is in allowed list
        if email not in ALLOWED_EMAILS:
            return jsonify({
                'success': False,
                'message': 'Unauthorized email ID. Only assigned faculty emails are allowed.'
            }), 401
        
        conn = get_db()
        cursor = conn.cursor()
        
        # Check if user exists
        cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
        user = cursor.fetchone()
        
        if role == 'hod':
            # HOD login - must be exact email and password
            if email != 'deveshsir@geu.ac.in':
                conn.close()
                return jsonify({
                    'success': False,
                    'message': 'Invalid HOD email ID'
                }), 401
            
            if not user or user['password'] != password or user['role'] != 'hod':
                conn.close()
                return jsonify({
                    'success': False,
                    'message': 'Invalid credentials'
                }), 401
            
            conn.close()
            return jsonify({
                'success': True,
                'message': 'Login successful',
                'role': 'hod',
                'email': email,
                'name': user['name']
            })
        
        elif role == 'faculty':
            # Email already validated in ALLOWED_EMAILS check
            if not user:
                conn.close()
                return jsonify({
                    'success': False,
                    'message': 'Faculty account not found. Contact administrator.'
                }), 401
            
            # First time login - set password
            if user['first_login'] == 1:
                cursor.execute('''
                    UPDATE users 
                    SET password = ?, first_login = 0 
                    WHERE email = ?
                ''', (password, email))
                conn.commit()
                conn.close()
                return jsonify({
                    'success': True,
                    'message': 'Password set successfully! Please remember this password for future logins.',
                    'role': 'faculty',
                    'email': email,
                    'name': user['name'],
                    'first_login': True
                })
            
            # Subsequent login - verify password
            else:
                if user['password'] != password:
                    conn.close()
                    return jsonify({
                        'success': False,
                        'message': 'Invalid password'
                    }), 401
                
                conn.close()
                return jsonify({
                    'success': True,
                    'message': 'Login successful',
                    'role': 'faculty',
                    'email': email,
                    'name': user['name'],
                    'first_login': False
                })
        
        conn.close()
        return jsonify({
            'success': False,
            'message': 'Invalid role'
        }), 400
        
    except Exception as e:
        print(f"Login error: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Server error'
        }), 500

@app.route('/api/faculty', methods=['GET'])
def get_faculty():
    """Get all unique faculty names from timetable"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT DISTINCT faculty FROM timetable ORDER BY faculty')
    rows = cursor.fetchall()
    
    faculty_list = [row['faculty'] for row in rows]
    conn.close()
    return jsonify(faculty_list)

@app.route('/api/rooms', methods=['GET'])
def get_rooms():
    """Get all unique rooms from timetable"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT DISTINCT room FROM timetable ORDER BY room')
    rows = cursor.fetchall()
    
    rooms = []
    for row in rows:
        room_name = row['room']
        # Determine room type and capacity based on prefix
        if room_name.startswith('A') or room_name.startswith('B'):
            room_type = 'Classroom'
            capacity = '60 Students'
        elif room_name.startswith('Lab'):
            room_type = 'Computer Lab'
            capacity = '35 Systems'
        elif room_name.startswith('Seminar'):
            room_type = 'Seminar Hall'
            capacity = '100 People'
        elif room_name.startswith('LT'):
            room_type = 'Lecture Theatre'
            capacity = '80 Students'
        else:
            room_type = 'Room'
            capacity = 'Various'
        
        rooms.append({
            'id': room_name,
            'type': room_type,
            'capacity': capacity
        })
    
    conn.close()
    return jsonify(rooms)

@app.route('/api/timetable', methods=['GET'])
def get_timetable():
    """Get full timetable"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM timetable')
    rows = cursor.fetchall()
    
    timetable = [{
        'day': row['day'],
        'time': row['time'],
        'room': row['room'],
        'faculty': row['faculty'],
        'subject': row['subject'],
        'semester': row['semester']
    } for row in rows]
    
    conn.close()
    return jsonify(timetable)

@app.route('/api/empty', methods=['GET'])
def get_empty_rooms():
    """Check available rooms for given day and time"""
    day = request.args.get('day', '')
    time = request.args.get('time', '')
    
    all_rooms = ['C-101', 'L-201', 'SH-301']
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT room FROM timetable 
        WHERE day = ? AND time = ?
    ''', (day, time))
    
    occupied = [row['room'] for row in cursor.fetchall()]
    
    cursor.execute('''
        SELECT DISTINCT room FROM bookings 
        WHERE status = 'approved' 
        AND datetime(start_time) <= datetime(?)
        AND datetime(end_time) >= datetime(?)
    ''', (f"{day} {time}", f"{day} {time}"))
    
    booked = [row['room'] for row in cursor.fetchall()]
    
    conn.close()
    
    occupied_rooms = set(occupied + booked)
    empty_rooms = [room for room in all_rooms if room not in occupied_rooms]
    
    return jsonify({'empty_rooms': empty_rooms})

@app.route('/api/bookings', methods=['GET'])
def get_bookings():
    """Get all bookings with OS CONCEPT: Reader-Writer Lock + Priority Scheduling with Aging"""
    faculty = request.args.get('faculty')
    status = request.args.get('status')
    
    # OS CONCEPT: READER LOCK - Multiple reads can happen simultaneously
    timetable_rw_lock.acquire_read()
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        query = 'SELECT * FROM bookings'
        params = []
        
        conditions = []
        if faculty:
            conditions.append('faculty = ?')
            params.append(faculty)
        if status:
            conditions.append('status = ?')
            params.append(status)
        
        if conditions:
            query += ' WHERE ' + ' AND '.join(conditions)
        
        # OS CONCEPT: PRIORITY SCHEDULING - Order by calculated priority (with aging)
        query += ' ORDER BY status ASC, id DESC'
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        bookings = []
        for row in rows:
            booking = {
                'id': row['id'],
                'faculty': row['faculty'],
                'facultyName': row['faculty_name'] if 'faculty_name' in row.keys() else '',
                'subject': row['subject'] if 'subject' in row.keys() else '',
                'room': row['room'],
                'purpose': row['purpose'],
                'start': row['start_time'],
                'end': row['end_time'],
                'notes': row['notes'],
                'status': row['status'],
                'priority': row['priority'] if 'priority' in row.keys() else 'normal',
                'timestamp': row['timestamp'],
                'approvedAt': row['approved_at'],
                'rejectedAt': row['rejected_at']
            }
            
            # Calculate effective priority with aging for pending requests
            if row['status'] == 'pending':
                base_priority = PRIORITY_LEVELS.get(booking['priority'], 3)
                effective_priority = calculate_priority_with_aging(base_priority, row['timestamp'])
                booking['effectivePriority'] = effective_priority
                booking['waitingHours'] = round((datetime.now() - datetime.fromisoformat(row['timestamp'])).total_seconds() / 3600, 1)
            
            bookings.append(booking)
        
        # Sort pending bookings by effective priority
        if status == 'pending':
            bookings.sort(key=lambda x: x.get('effectivePriority', 999))
        
        conn.close()
        return jsonify(bookings)
    
    finally:
        # OS CONCEPT: Release reader lock
        timetable_rw_lock.release_read()

@app.route('/api/request', methods=['POST'])
def create_booking():
    """Create new booking request with OS concepts: Semaphore, Mutex, Deadlock Prevention"""
    # OS CONCEPT 1: SEMAPHORE - Limit concurrent booking requests
    if not booking_semaphore.acquire(blocking=False):
        return jsonify({
            'success': False,
            'message': 'System busy. Too many concurrent booking requests. Please try again.'
        }), 503
    
    try:
        data = request.json
        room_id = data['room']
        
        # OS CONCEPT 2: DEADLOCK PREVENTION - Resource ordering (lock rooms alphabetically)
        room_lock = get_room_lock(room_id)
        
        # OS CONCEPT 3: MUTEX LOCK - Critical section for room booking
        with room_lock:
            # OS CONCEPT 4: DATABASE TRANSACTION with exclusive lock
            conn = sqlite3.connect(DATABASE, timeout=10.0)
            conn.row_factory = sqlite3.Row
            
            try:
                conn.execute('BEGIN EXCLUSIVE')  # Prevent race conditions
                cursor = conn.cursor()
                
                # Double-check room availability within transaction
                cursor.execute('''
                    SELECT COUNT(*) FROM bookings 
                    WHERE room = ? AND status = 'approved' 
                    AND ((start_time <= ? AND end_time > ?) OR (start_time < ? AND end_time >= ?))
                ''', (room_id, data['start'], data['start'], data['end'], data['end']))
                
                if cursor.fetchone()[0] > 0:
                    conn.rollback()
                    return jsonify({
                        'success': False,
                        'message': 'Room was just booked by someone else. Please refresh and try again.'
                    }), 409
                
                # OS CONCEPT 5: PRIORITY SCHEDULING - Default priority is 'normal'
                priority = data.get('priority', 'normal')
                
                cursor.execute('''
                    INSERT INTO bookings (faculty, faculty_name, room, subject, purpose, start_time, end_time, notes, status, priority, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                ''', (
                    data['faculty'],
                    data.get('facultyName', ''),
                    room_id,
                    data.get('subject', ''),
                    data['purpose'],
                    data['start'],
                    data['end'],
                    data.get('notes', ''),
                    priority,
                    datetime.now().isoformat()
                ))
                
                conn.commit()
                booking_id = cursor.lastrowid
                
                return jsonify({
                    'success': True,
                    'id': booking_id,
                    'message': f'Booking request submitted successfully with {priority} priority'
                })
                
            except sqlite3.OperationalError as e:
                conn.rollback()
                return jsonify({
                    'success': False,
                    'message': 'Database temporarily locked. Please retry.'
                }), 503
            except Exception as e:
                conn.rollback()
                print(f"Booking error: {str(e)}")
                return jsonify({
                    'success': False,
                    'message': 'Failed to create booking'
                }), 500
            finally:
                conn.close()
    
    finally:
        # OS CONCEPT: Release semaphore
        booking_semaphore.release()

@app.route('/api/approve/<int:booking_id>', methods=['POST'])
def approve_booking(booking_id):
    """Approve a booking request"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE bookings 
        SET status = 'approved', approved_at = ?
        WHERE id = ?
    ''', (datetime.now().isoformat(), booking_id))
    
    conn.commit()
    
    cursor.execute('SELECT * FROM bookings WHERE id = ?', (booking_id,))
    row = cursor.fetchone()
    
    conn.close()
    
    if row:
        return jsonify({
            'success': True,
            'message': f"Booking for {row['room']} has been approved"
        })
    else:
        return jsonify({'success': False, 'message': 'Booking not found'}), 404

@app.route('/api/reject/<int:booking_id>', methods=['POST'])
def reject_booking(booking_id):
    """Reject a booking request"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE bookings 
        SET status = 'rejected', rejected_at = ?
        WHERE id = ?
    ''', (datetime.now().isoformat(), booking_id))
    
    conn.commit()
    
    cursor.execute('SELECT * FROM bookings WHERE id = ?', (booking_id,))
    row = cursor.fetchone()
    
    conn.close()
    
    if row:
        return jsonify({
            'success': True,
            'message': f"Booking for {row['room']} has been rejected"
        })
    else:
        return jsonify({'success': False, 'message': 'Booking not found'}), 404

if __name__ == '__main__':
    print("🌐 GEU Smart Classroom Booking System ")
    print("=" * 60)
    
    # Initialize database
    init_db()
    
    # Load timetable from CSV
    load_timetable_from_csv()
    
    print("=" * 60)
    print("Server starting on http://localhost:8080")
    print("=" * 60)
    
    # Run Flask app
    app.run(host='0.0.0.0', port=8080, debug=False)