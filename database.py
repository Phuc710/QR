import sqlite3
import hashlib

class Database:
    def __init__(self, db_name="smart_parking.db"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._initialize_db()

    def _hash_password(self, password):
        return hashlib.sha256(password.encode()).hexdigest()

    def _initialize_db(self):
        self.cursor.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS parking_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                license_plate TEXT NOT NULL,
                rfid_id TEXT,
                entry_time DATETIME NOT NULL,
                exit_time DATETIME,
                fee INTEGER,
                status TEXT NOT NULL,
                payment_status TEXT,
                entry_image_path TEXT,
                exit_image_path TEXT,
                employee_entry TEXT,
                employee_exit TEXT
            );
            
            CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);
            CREATE INDEX IF NOT EXISTS idx_parking_license_plate ON parking_history (license_plate);
            CREATE INDEX IF NOT EXISTS idx_parking_status ON parking_history (status);
        ''')
        
        self.cursor.execute("SELECT COUNT(*) FROM users")
        if self.cursor.fetchone()[0] == 0:
            users_data = [
                ("admin", self._hash_password("admin"), "Quản trị viên", "admin"),
                ("user", self._hash_password("123"), "Nhân Viên A", "user")
            ]
            self.cursor.executemany(
                "INSERT INTO users (username, password, full_name, role) VALUES (?, ?, ?, ?)",
                users_data
            )
        self.conn.commit()

    def check_user(self, username, password):
        hashed_password = self._hash_password(password)
        self.cursor.execute(
            "SELECT id, full_name, role FROM users WHERE username = ? AND password = ?",
            (username, hashed_password)
        )
        return self.cursor.fetchone()

    def get_users(self):
        self.cursor.execute("SELECT id, username, full_name, role FROM users")
        return self.cursor.fetchall()

    def add_user(self, username, password, full_name, role):
        try:
            hashed_password = self._hash_password(password)
            self.cursor.execute(
                "INSERT INTO users (username, password, full_name, role) VALUES (?, ?, ?, ?)",
                (username, hashed_password, full_name, role)
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def update_user(self, user_id, username, password, full_name, role):
        try:
            if password:
                hashed_password = self._hash_password(password)
                query = "UPDATE users SET username=?, password=?, full_name=?, role=? WHERE id=?"
                params = (username, hashed_password, full_name, role, user_id)
            else:
                query = "UPDATE users SET username=?, full_name=?, role=? WHERE id=?"
                params = (username, full_name, role, user_id)
            
            self.cursor.execute(query, params)
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def delete_user(self, user_id):
        self.cursor.execute("DELETE FROM users WHERE id=?", (user_id,))
        self.conn.commit()

    def log_car_entry(self, plate, rfid, entry_time, image_path, employee_name):
        self.cursor.execute(
            "INSERT INTO parking_history (license_plate, rfid_id, entry_time, status, payment_status, entry_image_path, employee_entry) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (plate, rfid, entry_time, "Trong bãi", "Chưa thanh toán", image_path, employee_name)
        )
        self.conn.commit()
        return self.cursor.lastrowid
    
    def find_active_vehicle(self, plate):
        self.cursor.execute(
            "SELECT id, license_plate, rfid_id, entry_time FROM parking_history WHERE license_plate = ? AND status = 'Trong bãi' ORDER BY entry_time DESC LIMIT 1",
            (plate,)
        )
        return self.cursor.fetchone()

    def log_car_exit(self, record_id, exit_time, fee, image_path, employee_name):
        self.cursor.execute(
            "UPDATE parking_history SET exit_time=?, fee=?, status=?, payment_status=?, exit_image_path=?, employee_exit=? WHERE id=?",
            (exit_time, fee, "Đã ra", "Đã thanh toán", image_path, employee_name, record_id)
        )
        self.conn.commit()
    
    def get_history(self, plate_filter=None, date_filter=None):
        query = "SELECT id, license_plate, rfid_id, entry_time, exit_time, fee, status, payment_status, employee_entry, employee_exit FROM parking_history"
        params = []
        conditions = []

        if plate_filter:
            conditions.append("license_plate LIKE ?")
            params.append(f"%{plate_filter}%")
        
        if date_filter:
            conditions.append("DATE(entry_time) = ?")
            params.append(date_filter)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY entry_time DESC"
        self.cursor.execute(query, params)
        return self.cursor.fetchall()

    def delete_history(self, record_id):
        self.cursor.execute("DELETE FROM parking_history WHERE id=?", (record_id,))
        self.conn.commit()
    
    def get_revenue_report(self, start_date, end_date):
        self.cursor.execute(
            "SELECT COALESCE(SUM(fee), 0) FROM parking_history WHERE status = 'Đã ra' AND DATE(exit_time) BETWEEN ? AND ?",
            (start_date, end_date)
        )
        return self.cursor.fetchone()[0]

    def close(self):
        self.conn.close()