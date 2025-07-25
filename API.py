# app_backend.py (ví dụ)
from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import paho.mqtt.client as mqtt
import json
import threading
import logging
from database import Database # Import database của bạn
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
CORS(app) # Cho phép CORS nếu frontend và backend chạy trên các domain/port khác nhau
socketio = SocketIO(app, cors_allowed_origins="*") # Cho phép tất cả các nguồn gốc (cho dev)

# --- Cấu hình MQTT ---
MQTT_BROKER_HOST = "0.tcp.ngrok.io" # Thay bằng host Ngrok TCP của bạn
MQTT_BROKER_PORT = "1000" # Thay bằng cổng Ngrok TCP của bạn
MQTT_TOPIC_DATA = "parking/data"
MQTT_TOPIC_COMMAND = "parking/command"
MQTT_TOPIC_ALERT = "parking/alert"

mqtt_client = mqtt.Client()
db = Database() # Khởi tạo database của bạn

def on_mqtt_connect(client, userdata, flags, rc):
    logging.info(f"Connected to MQTT Broker with result code {rc}")
    client.subscribe(MQTT_TOPIC_DATA)
    client.subscribe(MQTT_TOPIC_ALERT)
    logging.info(f"Subscribed to {MQTT_TOPIC_DATA} and {MQTT_TOPIC_ALERT}")

def on_mqtt_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        logging.info(f"MQTT Message received - Topic: {msg.topic}, Data: {data}")

        # Broadcast MQTT data to connected web clients via Socket.IO
        socketio.emit('mqtt_data', data)

        # Process parking events and log to DB
        event = data.get('event')
        if event == "CAR:DETECT_IN":
            # Assume data contains license_plate, rfid_id, etc.
            # Example: self.db.log_car_entry(...)
            # For now, just emit to dashboard
            socketio.emit('vehicle_info_update', {
                'license_plate': data.get('value', 'N/A'), # Example, adjust based on actual payload
                'status': 'Đang vào',
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'message': 'Phát hiện xe vào'
            })
            # Call DB logic here, for example:
            # db.log_car_entry(data['license_plate'], data['rfid_id'], datetime.now(), "path/to/image.jpg", "System")

        elif event == "CAR:DETECT_OUT":
             socketio.emit('vehicle_info_update', {
                'license_plate': data.get('value', 'N/A'),
                'status': 'Đang ra',
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'message': 'Phát hiện xe ra'
            })
            # Call DB logic here

        elif event == "RFID_IN" or event == "RFID_OUT":
             socketio.emit('vehicle_info_update', {
                'rfid_id': data.get('value', 'N/A'),
                'message': f'RFID detected: {data.get("value", "N/A")}'
            })

        elif event == "PARKING:FULL":
            socketio.emit('dashboard_stats_update', {'parking_status': 'BÃI FULL'})
            socketio.emit('mqtt_data', {'event': 'PARKING:FULL', 'type': 'full'}) # Send alert to frontend

        elif event == "ALERT":
            socketio.emit('mqtt_data', {'event': 'ALERT', 'type': data.get('type', 'Unknown alert')})

        # Sau khi xử lý xong một sự kiện xe vào/ra, cập nhật lại các chỉ số tổng quan
        update_dashboard_stats()

    except Exception as e:
        logging.error(f"Error processing MQTT message: {e} - Payload: {msg.payload.decode()}")

mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message

# Khởi động luồng MQTT
def start_mqtt_loop():
    try:
        mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
        mqtt_client.loop_forever()
    except Exception as e:
        logging.error(f"Could not connect to MQTT broker: {e}")

mqtt_thread = threading.Thread(target=start_mqtt_loop, daemon=True)
mqtt_thread.start()

# --- API Endpoints ---
@app.route('/')
def home():
    return "X Parking Backend API is running."

@app.route('/api/dashboard-stats', methods=['GET'])
def get_dashboard_stats():
    current_vehicles_count = db.cursor.execute("SELECT COUNT(*) FROM parking_history WHERE status = 'Trong bãi'").fetchone()[0]
    
    today_start = datetime.now().strftime('%Y-%m-%d 00:00:00')
    today_end = datetime.now().strftime('%Y-%m-%d 23:59:59')
    today_total_vehicles = db.cursor.execute("SELECT COUNT(*) FROM parking_history WHERE entry_time BETWEEN ? AND ?", (today_start, today_end)).fetchone()[0]
    today_revenue = db.get_revenue_report(datetime.now().strftime('%Y-%m-%d'), datetime.now().strftime('%Y-%m-%d'))

    # Dummy hourly data for chart (replace with actual data from DB if available)
    hourly_data = [db.cursor.execute(
        "SELECT COUNT(*) FROM parking_history WHERE strftime('%H', entry_time) = ? AND DATE(entry_time) = ?",
        (f'{i:02d}', datetime.now().strftime('%Y-%m-%d'))
    ).fetchone()[0] for i in range(24)]

    return jsonify({
        'current_vehicles': current_vehicles_count,
        'today_total': today_total_vehicles,
        'today_revenue': today_revenue,
        'parking_status': 'Sẵn sàng' if current_vehicles_count < 100 else 'BÃI FULL', # Example logic
        'hourly_data': hourly_data # For chart
    })

@app.route('/api/recent-activities', methods=['GET'])
def get_recent_activities():
    # Lấy 10 bản ghi lịch sử gần nhất, có thể tùy chỉnh
    activities = db.get_history(plate_filter=None, date_filter=None) # Lấy tất cả, sau đó giới hạn
    
    formatted_activities = []
    for activity in activities[:10]: # Lấy 10 cái mới nhất
        id, plate, rfid, entry_time_str, exit_time_str, fee, status, payment_status, _, _ = activity
        
        message = f"Xe {plate} "
        if status == "Trong bãi":
            message += f"đã vào bãi lúc {datetime.strptime(entry_time_str, '%Y-%m-%d %H:%M:%S.%f').strftime('%H:%M:%S')}"
        elif status == "Đã ra":
            message += f"đã ra bãi lúc {datetime.strptime(exit_time_str, '%Y-%m-%d %H:%M:%S.%f').strftime('%H:%M:%S')}, phí {fee:,} VNĐ"
        
        formatted_activities.append({
            'license_plate': plate,
            'rfid_id': rfid,
            'status': status,
            'timestamp': (exit_time_str or entry_time_str), # Use exit if available, else entry
            'message': message
        })
    return jsonify(formatted_activities)

@app.route('/api/control/barrier_in_open', methods=['POST'])
def barrier_in_open():
    mqtt_client.publish(MQTT_TOPIC_COMMAND, "BARRIER_IN_OPEN")
    logging.info("Sent command: BARRIER_IN_OPEN")
    return jsonify({'success': True, 'message': 'Lệnh mở barrier vào đã được gửi.'})

@app.route('/api/control/barrier_out_open', methods=['POST'])
def barrier_out_open():
    mqtt_client.publish(MQTT_TOPIC_COMMAND, "BARRIER_OUT_OPEN")
    logging.info("Sent command: BARRIER_OUT_OPEN")
    return jsonify({'success': True, 'message': 'Lệnh mở barrier ra đã được gửi.'})

@app.route('/api/control/emergency_stop', methods=['POST'])
def emergency_stop():
    mqtt_client.publish(MQTT_TOPIC_COMMAND, "EMERGENCY_STOP")
    logging.info("Sent command: EMERGENCY_STOP")
    return jsonify({'success': True, 'message': 'Lệnh dừng khẩn cấp đã được gửi.'})

# Socket.IO events (for web clients connecting to this Flask app)
@socketio.on('connect')
def test_connect():
    logging.info('Client connected to Socket.IO')
    # Gửi trạng thái dashboard ban đầu khi có client mới kết nối
    update_dashboard_stats()

@socketio.on('disconnect')
def test_disconnect():
    logging.info('Client disconnected from Socket.IO')

def update_dashboard_stats():
    with app.app_context(): # Cần context để truy cập các resource của Flask app
        stats = get_dashboard_stats().json
        socketio.emit('dashboard_stats_update', stats)
        # Emit 'parking_status' separately as well if needed
        socketio.emit('mqtt_data', {'event': 'PARKING_STATUS_UPDATE', 'value': stats['parking_status']})

if __name__ == '__main__':
    # Chạy Flask app với Socket.IO
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
    # Public Flask app qua Ngrok HTTP
    # ngrok http 5000