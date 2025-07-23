import requests
import urllib.parse
import threading
import time
import uuid
import json
from datetime import datetime, timedelta
import logging

class PaymentManager:
    def __init__(self, config):
        self.config = config
        self.active_sessions = {}
        self._lock = threading.Lock()
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    def generate_unique_description(self, license_plate, hours):
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        return f"BSX_{license_plate}_{hours}H_{timestamp}_{unique_id}"

    def generate_vietqr_url(self, amount, description, account_name):
        bank_id = self.config['bank_id']
        account_no = self.config['account_no']
        encoded_description = urllib.parse.quote(description)
        encoded_account_name = urllib.parse.quote(account_name)
        return f"https://img.vietqr.io/image/{bank_id}-{account_no}-compact.png?amount={amount}&addInfo={encoded_description}&accountName={encoded_account_name}"

    def check_payment_status(self, amount, description, start_time):
        try:
            payload = {
                "amount": amount,
                "description": description,
                "start_time": start_time.isoformat()
            }
            response = requests.post(
                f"{self.config['script_url']}?action=checkPayment",
                json=payload,
                timeout=10
            )
            if response.status_code == 200:
                result = response.json()
                return result.get('found', False), result.get('data', {})
            return False, {}
        except Exception as e:
            logging.error(f"Lỗi khi kiểm tra trạng thái thanh toán: {e}")
            return False, {}

    def _payment_check_thread(self, session_id, amount, description, start_time, on_success, on_timeout):
        while session_id in self.active_sessions:
            if time.time() - start_time.timestamp() > self.config['max_wait_time']:
                with self._lock:
                    if session_id in self.active_sessions:
                        del self.active_sessions[session_id]
                on_timeout()
                return

            found, transaction_data = self.check_payment_status(amount, description, start_time)
            if found:
                with self._lock:
                    if session_id in self.active_sessions:
                        del self.active_sessions[session_id]
                on_success(transaction_data)
                return

            time.sleep(self.config['check_interval'])

    def start_payment_flow(self, vehicle_data, total_fee, on_success, on_timeout):
        license_plate = vehicle_data.get('license_plate', '')
        hours = vehicle_data.get('hours', 0)
        description = self.generate_unique_description(license_plate, hours)
        qr_url = self.generate_vietqr_url(total_fee, description, self.config['account_name'])

        start_time = datetime.now()
        session_id = str(uuid.uuid4())

        with self._lock:
            self.active_sessions[session_id] = {
                'description': description,
                'amount': total_fee,
                'start_time': start_time,
                'vehicle_data': vehicle_data
            }

        thread = threading.Thread(
            target=self._payment_check_thread,
            args=(session_id, total_fee, description, start_time, on_success, on_timeout),
            daemon=True
        )
        thread.start()

        return {
            'session_id': session_id,
            'qr_url': qr_url,
            'description': description,
            'amount': total_fee,
            'start_time': start_time.isoformat()
        }

    def cancel_payment(self, session_id):
        with self._lock:
            if session_id in self.active_sessions:
                del self.active_sessions[session_id]
                return True
        return False

    def get_active_sessions(self):
        with self._lock:
            return list(self.active_sessions.keys())

    def cleanup_expired_sessions(self):
        current_time = datetime.now()
        expired_sessions = []

        with self._lock:
            for session_id, session_data in self.active_sessions.items():
                if (current_time - session_data['start_time']).seconds > self.config['max_wait_time']:
                    expired_sessions.append(session_id)

            for session_id in expired_sessions:
                del self.active_sessions[session_id]

        return len(expired_sessions)