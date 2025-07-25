import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from PIL import Image, ImageTk
import cv2
import threading
import time
import os
import logging
from datetime import datetime
import math
from tkcalendar import DateEntry
from payment import PaymentManager
import urllib.request
import paho.mqtt.client as mqtt
import json
import smtplib
from email.mime.text import MIMEText
LPR_AVAILABLE = False
try:
    from QUET_BSX import OptimizedLPR
    LPR_AVAILABLE = True
except ImportError:
    logging.warning("Thư viện QUET_BSX không tìm thấy. Chức năng nhận dạng biển số sẽ bị vô hiệu hóa.")
class MainApplication:
    def __init__(self, root, db_connection):
        self.root = root
        self.db = db_connection
        self.lpr_system = None
        if LPR_AVAILABLE:
            self.lpr_system = OptimizedLPR()
        self.current_user = None
        self.current_screen = None
        self.vid_in, self.vid_out = None, None
        self.latest_frame_in = None
        self.latest_frame_out = None
        self.frame_lock_in = threading.Lock()
        self.frame_lock_out = threading.Lock()
        self.camera_thread_in = None
        self.camera_thread_out = None
        self.is_running = False
        self.current_frame_in, self.current_frame_out = None, None
        self._camera_update_id = None
        self.active_vehicle_id = None
        self.payment_config = {
            'script_url': 'https://script.google.com/macros/s/AKfycbxchRS9MGnEfn2SC_56vLhX04Hz_5BsN0VDQs4P8bN07dzOyd2S5rqHO9efTJcPbisi/exec',
            'bank_id': 'MB',
            'account_no': '0396032433',
            'account_name': 'NGO VAN CHIEU',
            'max_wait_time': 300
        }
        self.payment_manager = PaymentManager(self.payment_config)
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=90)  # Tối ưu reconnect nhanh
        self.mqtt_client.connect("192.168.1.4", 1883, 60)
        threading.Thread(target=self.mqtt_client.loop_forever, daemon=True).start()
        self.email_var = tk.StringVar(value="athanhphuc7102005@gmail.com")
        if not os.path.exists('anh'):
            os.makedirs('anh')
        logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    def on_mqtt_connect(self, client, userdata, flags, rc):
        client.subscribe("parking/data")
    def on_mqtt_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            event = data['event']
            if event == "CAR:DETECT_IN":
                self.root.after(0, self.process_car_entry)
            elif event == "RFID_IN":
                rfid = data['value']
                self.info_vars["ID Thẻ:"].set(rfid)
            elif event == "CAR:DETECT_OUT":
                self.root.after(0, self.process_car_exit)
            elif event == "RFID_OUT":
                rfid = data['value']
                self.info_vars["ID Thẻ:"].set(rfid)
            elif event == "ALERT":
                self.send_email("[ALERT] X PARKING", data['type'])
            elif event == "PARKING:FULL":
                self.info_vars["Trạng Thái:"].set("BÃI FULL")
            elif event == "SLOT_ASSIGN":
                slot = data['value']
                self.info_vars["Trạng Thái:"].set(f"Đỗ tại slot {slot}")
            elif event in ["CAR_IN_COMPLETE", "CAR_OUT_COMPLETE"]:
                self.root.after(0, self.reset_info_panel)
        except Exception as e:
            logging.error(f"MQTT message error: {e}")
    def send_email(self, subject, body):
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        sender_email = "Acc13422@gmail.com"
        sender_password = "dvdultkxshztqwth"
        recipient_email = self.email_var.get()
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = recipient_email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, msg.as_string())
    def load_models(self, update_progress_callback=None):
        def update(value):
            if update_progress_callback:
                self.root.after(0, update_progress_callback, value)
        logging.info("Bắt đầu tải mô hình LPR...")
        update(20)
        if self.lpr_system:
            success = self.lpr_system.load_models()
            if not success:
                logging.error("Không thể tải mô hình LPR.")
                self.root.after(0, self.show_model_load_error)
        update(80)
        time.sleep(1)
        logging.info("Tải mô hình hoàn tất.")
        update(100)
    def show_model_load_error(self):
        if messagebox.askretrycancel("Lỗi tải mô hình", "Không thể tải mô hình LPR. Thử lại?"):
            self.load_models()
    def start(self, user_info):
        self.current_user = user_info
        self.root.title("HỆ THỐNG BÃI XE THÔNG MINH")
        self.root.geometry('1200x650')
        self.root.resizable(True, True)
        self.root.configure(bg='#dcdcdc')
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.setup_styles()
        self.create_main_container()
        self.show_main_screen()
        self.root.update_idletasks()
        self.root.deiconify()
    def create_main_container(self):
        self.main_container = ttk.Frame(self.root, style='Main.TFrame')
        self.main_container.pack(fill=tk.BOTH, expand=True)
        self.header_frame = ttk.Frame(self.main_container, height=60)
        self.header_frame.pack(fill=tk.X)
        self.header_frame.grid_columnconfigure(0, weight=1)
        self.header_frame.grid_columnconfigure(1, weight=1)
        self.header_frame.grid_columnconfigure(2, weight=1)
        self.left_header_frame = ttk.Frame(self.header_frame)
        self.left_header_frame.grid(row=0, column=0, sticky='w', padx=10)
        self.center_header_frame = ttk.Frame(self.header_frame)
        self.center_header_frame.grid(row=0, column=1, sticky='ew')
        self.right_header_frame = ttk.Frame(self.header_frame)
        self.right_header_frame.grid(row=0, column=2, sticky='e', padx=10)
        self.title_label = ttk.Label(self.center_header_frame, text="HỆ THỐNG BÃI XE THÔNG MINH", style='Title.TLabel')
        self.title_label.pack(pady=12)
        try:
            self.logo_img_pil = Image.open("logo.png").resize((100, 80), Image.Resampling.LANCZOS)
            self.logo_img = ImageTk.PhotoImage(self.logo_img_pil)
            ttk.Label(self.right_header_frame, image=self.logo_img).pack()
        except FileNotFoundError:
            ttk.Label(self.right_header_frame, text="Logo not found").pack()
        self.content_frame = ttk.Frame(self.main_container, style='Content.TFrame')
        self.content_frame.pack(fill=tk.BOTH, expand=True)
    def show_main_screen(self):
        self.current_screen = "main"
        self.clear_content()
        self.show_user_menu()
        self.init_cameras()
        cam_in_frame = ttk.LabelFrame(self.content_frame, text="CAMERA VÀO")
        cam_in_frame.place(relx=0.02, rely=0.02, relwidth=0.3, relheight=0.4)
        self.camera_in_canvas = tk.Canvas(cam_in_frame, bg='grey')
        self.camera_in_canvas.pack(fill=tk.BOTH, expand=True)
        plate_in_img_frame = ttk.LabelFrame(self.content_frame, text="ẢNH CẮT BIỂN SỐ")
        plate_in_img_frame.place(relx=0.02, rely=0.45, relwidth=0.3, relheight=0.12)
        self.plate_in_canvas = tk.Canvas(plate_in_img_frame, bg='lightgrey')
        self.plate_in_canvas.pack(fill=tk.BOTH, expand=True)
        plate_in_text_frame = ttk.LabelFrame(self.content_frame, text="BIỂN SỐ PHÁT HIỆN")
        plate_in_text_frame.place(relx=0.02, rely=0.6, relwidth=0.3, relheight=0.13)
        self.plate_in_var = tk.StringVar(value="...")
        plate_in_label = ttk.Label(plate_in_text_frame, textvariable=self.plate_in_var, font=('Arial', 22, 'bold'), foreground=self.colors['primary'], anchor='center')
        plate_in_label.pack(fill=tk.BOTH, expand=True)
        cam_out_frame = ttk.LabelFrame(self.content_frame, text="CAMERA RA")
        cam_out_frame.place(relx=0.33, rely=0.02, relwidth=0.3, relheight=0.4)
        self.camera_out_canvas = tk.Canvas(cam_out_frame, bg='black')
        self.camera_out_canvas.pack(fill=tk.BOTH, expand=True)
        plate_out_img_frame = ttk.LabelFrame(self.content_frame, text="ẢNH CẮT BIỂN SỐ")
        plate_out_img_frame.place(relx=0.33, rely=0.45, relwidth=0.3, relheight=0.12)
        self.plate_out_canvas = tk.Canvas(plate_out_img_frame, bg='lightgrey')
        self.plate_out_canvas.pack(fill=tk.BOTH, expand=True)
        plate_out_text_frame = ttk.LabelFrame(self.content_frame, text="BIỂN SỐ PHÁT HIỆN")
        plate_out_text_frame.place(relx=0.33, rely=0.6, relwidth=0.3, relheight=0.13)
        self.plate_out_var = tk.StringVar(value="...")
        plate_out_label = ttk.Label(plate_out_text_frame, textvariable=self.plate_out_var, font=('Arial', 22, 'bold'), foreground=self.colors['primary'], anchor='center')
        plate_out_label.pack(fill=tk.BOTH, expand=True)
        info_frame = ttk.LabelFrame(self.content_frame, text="THÔNG TIN XE")
        info_frame.place(relx=0.65, rely=0.02, relwidth=0.33, relheight=0.4)
        info_frame.columnconfigure(1, weight=1)
        labels = ["Biển Số Xe:", "ID Thẻ:", "Trạng Thái:", "Thời gian:", "Tổng giờ gửi:", "Tổng Phí:", "Trạng Thái Thanh Toán:"]
        self.info_vars = {label: tk.StringVar(value="...") for label in labels}
        for i, txt in enumerate(labels):
            ttk.Label(info_frame, text=txt, style='Info.TLabel').grid(row=i, column=0, sticky='w', pady=4, padx=10)
            lbl = ttk.Label(info_frame, textvariable=self.info_vars[txt], font=('Arial', 12, 'bold'))
            lbl.grid(row=i, column=1, sticky='w', padx=10)
            if txt == "Tổng Phí:": lbl.config(foreground=self.colors['danger'])
            if txt == "Trạng Thái Thanh Toán:": lbl.config(foreground=self.colors['warning'])
            if txt == "Trạng Thái:": lbl.config(foreground=self.colors['secondary'])
        control_frame = ttk.LabelFrame(self.content_frame, text="ĐIỀU KHIỂN")
        control_frame.place(relx=0.65, rely=0.45, relwidth=0.33, relheight=0.25)
        btn_container = ttk.Frame(control_frame)
        btn_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.btn_car_entry = ttk.Button(btn_container, text="XE VÀO", style='Success.TButton', command=self.process_car_entry)
        self.btn_car_entry.pack(fill=tk.X, expand=True, pady=3)
        self.btn_car_exit = ttk.Button(btn_container, text="XE RA", style='Primary.TButton', command=self.process_car_exit)
        self.btn_car_exit.pack(fill=tk.X, expand=True, pady=3)
        self.reset_info_panel()
        if self._camera_update_id:
            self.root.after_cancel(self._camera_update_id)
        self.update_cameras()
    def _on_closing(self):
        if messagebox.askokcancel("Thoát", "Bạn có chắc chắn muốn thoát ứng dụng?"):
            self.payment_manager.cleanup_expired_sessions()
            self.release_cameras()
            self.mqtt_client.disconnect()
            self.root.destroy()
    def setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.colors = {'primary': '#2E86C1', 'secondary': '#28B463', 'danger': '#E74C3C', 'warning': '#F39C12', 'dark': "#1B3249", 'light': '#ECF0F1', 'white': '#FFFFFF'}
        self.style.configure('Main.TFrame', background='#dcdcdc')
        self.style.configure('Content.TFrame', background='#dcdcdc')
        self.style.configure('TLabel', background='#dcdcdc')
        self.style.configure('TLabelframe', background='#dcdcdc')
        self.style.configure('TLabelframe.Label', background='#dcdcdc')
        self.style.configure('Title.TLabel', font=('Arial', 24, 'bold'), foreground=self.colors['dark'])
        self.style.configure('Heading.TLabel', font=('Arial', 16, 'bold'), foreground=self.colors['primary'])
        self.style.configure('Info.TLabel', font=('Arial', 11))
        for btn_style, color in [('Primary', 'primary'), ('Success', 'secondary'), ('Danger', 'danger')]:
            self.style.configure(f'{btn_style}.TButton', font=('Arial', 12, 'bold'), foreground='white', background=self.colors[color], padding=10)
            self.style.map(f'{btn_style}.TButton', background=[('active', self.colors[color])])
    def clear_content(self, clear_header=False):
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        if clear_header:
            for widget in self.left_header_frame.winfo_children():
                widget.destroy()
    def show_user_menu(self):
        for widget in self.left_header_frame.winfo_children():
            widget.destroy()
        if self.current_user:
            role_map = {'admin': 'Admin', 'user': 'Nhân viên'}
            user_display_text = f"[{role_map.get(self.current_user['role'], 'User')}]: {self.current_user['name']}"
            menu_btn = ttk.Menubutton(self.left_header_frame, text=user_display_text, style='Primary.TButton')
            menu_btn.pack(side=tk.LEFT, padx=5)
            menu = tk.Menu(menu_btn, tearoff=0)
            menu_btn.config(menu=menu)
            menu.add_command(label="TRANG CHÍNH", command=self.show_main_screen)
            menu.add_separator()
            if self.current_user['role'] == 'admin':
                menu.add_command(label="LỊCH SỬ XE", command=self.show_history)
                menu.add_command(label="QUẢN LÝ NHÂN SỰ", command=self.show_staff_management)
                menu.add_command(label="BÁO CÁO DOANH THU", command=self.show_revenue_report)
                menu.add_separator()
            menu.add_command(label="ĐĂNG XUẤT", command=self.logout)
    def logout(self):
        if messagebox.askyesno("Đăng xuất", "Bạn có chắc chắn muốn đăng xuất và thoát chương trình?"):
            self._on_closing()
    def _camera_reader_thread(self, vid_capture, frame_storage_attr, lock):
        while self.is_running:
            if vid_capture and vid_capture.isOpened():
                ret, frame = vid_capture.read()
                if ret:
                    with lock:
                        setattr(self, frame_storage_attr, frame)
                else:
                    logging.warning(f"Không thể đọc frame từ camera của {frame_storage_attr}.")
            else:
                logging.error(f"Camera cho {frame_storage_attr} không mở hoặc không tồn tại.")
            time.sleep(1/30)
        logging.info(f"Luồng đọc camera cho {frame_storage_attr} đã dừng.")
    def init_cameras(self):
        self.release_cameras()
        self.vid_in = cv2.VideoCapture(0)
        self.vid_out = cv2.VideoCapture(1)
        self.is_running = True
        self.camera_thread_in = threading.Thread(target=self._camera_reader_thread, args=(self.vid_in, 'latest_frame_in', self.frame_lock_in), daemon=True)
        self.camera_thread_out = threading.Thread(target=self._camera_reader_thread, args=(self.vid_out, 'latest_frame_out', self.frame_lock_out), daemon=True)
        self.camera_thread_in.start()
        self.camera_thread_out.start()
        logging.info("Các luồng đọc camera đã bắt đầu.")
    def update_cameras(self):
        if self.current_screen != "main":
            return
        with self.frame_lock_in:
            frame_in = self.latest_frame_in.copy() if self.latest_frame_in is not None else None
        with self.frame_lock_out:
            frame_out = self.latest_frame_out.copy() if self.latest_frame_out is not None else None
        self.current_frame_in = frame_in
        self.current_frame_out = frame_out
        self.update_single_camera_display(self.current_frame_in, self.camera_in_canvas, 'grey')
        self.update_single_camera_display(self.current_frame_out, self.camera_out_canvas, 'black')
        self._camera_update_id = self.root.after(30, self.update_cameras)
    def update_single_camera_display(self, frame, canvas, error_bg_color):
        if frame is not None:
            canvas_w = canvas.winfo_width()
            canvas_h = canvas.winfo_height()
            if canvas_w > 1 and canvas_h > 1:
                try:
                    frame_resized = cv2.resize(frame, (canvas_w, canvas_h))
                    rgb_frame = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(rgb_frame)
                    photo = ImageTk.PhotoImage(image=img)
                    canvas.create_image(0, 0, image=photo, anchor=tk.NW)
                    canvas.image = photo
                except Exception as e:
                    logging.error(f"Lỗi khi hiển thị frame: {e}")
                    self.create_no_camera_display(canvas, "LỖI HIỂN THỊ", error_bg_color)
                return
        self.create_no_camera_display(canvas, "LỖI CAMERA", error_bg_color)
    def release_cameras(self):
        self.is_running = False
        if self._camera_update_id:
            self.root.after_cancel(self._camera_update_id)
            self._camera_update_id = None
        if self.camera_thread_in and self.camera_thread_in.is_alive():
            self.camera_thread_in.join(timeout=1.0)
        if self.camera_thread_out and self.camera_thread_out.is_alive():
            self.camera_thread_out.join(timeout=1.0)
        if self.vid_in: self.vid_in.release()
        if self.vid_out: self.vid_out.release()
        self.vid_in, self.vid_out = None, None
        logging.info("Cameras and reader threads released.")
    def create_no_camera_display(self, canvas, message, bg_color):
        canvas_w, canvas_h = canvas.winfo_width(), canvas.winfo_height()
        canvas.delete("all")
        if canvas_w > 1 and canvas_h > 1:
            canvas.create_rectangle(0, 0, canvas_w, canvas_h, fill=bg_color)
            canvas.create_text(canvas_w/2, canvas_h/2, text=message, fill="white", font=("Arial", 20, "bold"))
    def detect_license_plate(self, frame, canvas, output_widget):
        if not (self.lpr_system and self.lpr_system.is_ready()):
            self.set_widget_text(output_widget, "LPR Lỗi")
            return None
        result = self.lpr_system.detect_and_read_plate(frame)
        if result['success'] and result['plates']:
            best_plate = self.lpr_system.get_best_plate(result)
            if best_plate:
                self.root.after(0, lambda: self.display_plate_image(best_plate['cropped_image'], canvas))
                self.set_widget_text(output_widget, best_plate['text'])
                return best_plate['text']
        self.set_widget_text(output_widget, "Không thấy")
        self.root.after(0, lambda: self.clear_plate_image(canvas))
        return None
    def set_widget_text(self, widget, text):
        def command():
            if isinstance(widget, tk.StringVar):
                widget.set(text)
            elif isinstance(widget, ttk.Entry):
                widget.delete(0, tk.END)
                widget.insert(0, text)
        self.root.after(0, command)
    def display_plate_image(self, plate_img, canvas):
        canvas.delete("all")
        canvas_w, canvas_h = canvas.winfo_width(), canvas.winfo_height()
        if canvas_w < 2 or canvas_h < 2:
            self.root.after(50, lambda: self.display_plate_image(plate_img, canvas))
            return
        plate_resized = cv2.resize(plate_img, (canvas_w, canvas_h), interpolation=cv2.INTER_AREA)
        rgb_img = cv2.cvtColor(plate_resized, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb_img)
        photo = ImageTk.PhotoImage(image=img)
        canvas.create_image(0, 0, image=photo, anchor=tk.NW)
        canvas.image = photo
    def clear_plate_image(self, canvas):
        canvas.delete("all")
    def reset_info_panel(self):
        self.active_vehicle_id = None
        for key in self.info_vars:
            self.info_vars[key].set(".....")
        self.info_vars["Trạng Thái:"].set(".....")
        self.info_vars["Tổng Phí:"].set("0 VNĐ")
        self.plate_in_var.set(".....")
        self.plate_out_var.set(".....")
        self.clear_plate_image(self.plate_in_canvas)
        self.clear_plate_image(self.plate_out_canvas)
        if hasattr(self, 'btn_car_entry'):
            self.btn_car_entry.config(state=tk.NORMAL)
        if hasattr(self, 'btn_car_exit'):
            self.btn_car_exit.config(state=tk.NORMAL)
    def process_car_entry(self):
        self.info_vars["Trạng Thái:"].set("Đang xử lý VÀO...")
        frame = self.current_frame_in
        if frame is None:
            messagebox.showwarning("Lỗi", "Không có hình ảnh từ camera vào.")
            self.reset_info_panel()
            return
        threading.Thread(target=self._process_car_entry_thread, args=(frame,), daemon=True).start()
    def _process_car_entry_thread(self, frame):
        plate_text = self.detect_license_plate(frame, self.plate_in_canvas, self.plate_in_var)
        self.root.after(0, self.finalize_car_entry, plate_text, frame)
    def finalize_car_entry(self, plate_text, frame):
        if not plate_text:
            self.mqtt_client.publish("parking/command", "PLATE_ERROR_IN")
            messagebox.showerror("Lỗi nhận dạng", "Không nhận dạng được biển số xe. Vui lòng thử lại.")
            self.reset_info_panel()
            return
        plate_text = plate_text.upper()
        entry_time = datetime.now()
        image_name = f"VAO_{plate_text.replace('.', '')}_{entry_time.strftime('%Y%m%d%H%M%S')}.jpg"
        image_path = os.path.join('anh', image_name)
        cv2.imwrite(image_path, frame)
        rfid_placeholder = f"RFID_{int(time.time())}"
        self.active_vehicle_id = self.db.log_car_entry(plate_text, rfid_placeholder, entry_time, image_path, self.current_user['name'])
        self.info_vars["Biển Số Xe:"].set(plate_text)
        self.info_vars["ID Thẻ:"].set(rfid_placeholder)
        self.info_vars["Trạng Thái:"].set("Xe vào")
        self.info_vars["Thời gian:"].set(entry_time.strftime('%Y-%m-%d %H:%M:%S'))
        self.info_vars["Tổng giờ gửi:"].set("...")
        self.info_vars["Tổng Phí:"].set("...")
        self.info_vars["Trạng Thái Thanh Toán:"].set("...")
        self.mqtt_client.publish("parking/command", "PLATE_OK_IN")
    def process_car_exit(self):
        self.info_vars["Trạng Thái:"].set("Đang xử lý RA...")
        frame = self.current_frame_out
        if frame is None:
            messagebox.showwarning("Lỗi", "Không có hình ảnh từ camera ra.")
            self.reset_info_panel()
            return
        threading.Thread(target=self._process_car_exit_thread, args=(frame,), daemon=True).start()
    def _process_car_exit_thread(self, frame):
        plate_text = self.detect_license_plate(frame, self.plate_out_canvas, self.plate_out_var)
        self.root.after(0, self.finalize_car_exit, plate_text, frame)
    def finalize_car_exit(self, plate_text, frame):
        if not plate_text:
            self.mqtt_client.publish("parking/command", "PLATE_ERROR_OUT")
            messagebox.showerror("Lỗi nhận dạng", "Không nhận dạng được biển số xe. Vui lòng thử lại.")
            self.reset_info_panel()
            return
        vehicle_data = self.db.find_active_vehicle(plate_text.upper())
        if not vehicle_data:
            messagebox.showerror("Lỗi", f"Không tìm thấy xe {plate_text.upper()} trong bãi.")
            self.reset_info_panel()
            return
        self.active_vehicle_id = vehicle_data[0]
        db_plate = vehicle_data[1]
        db_rfid = vehicle_data[2]
        entry_time_str = vehicle_data[3]
        try:
            entry_time = datetime.strptime(entry_time_str, '%Y-%m-%d %H:%M:%S.%f')
        except ValueError:
            entry_time = datetime.strptime(entry_time_str, '%Y-%m-%d %H:%M:%S')
        exit_time = datetime.now()
        duration = exit_time - entry_time
        days = duration.days
        hours, remainder = divmod(duration.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        duration_str = ""
        if days > 0: duration_str += f"{days} ngày "
        if hours > 0: duration_str += f"{hours} giờ "
        duration_str += f"{minutes} phút"
        total_hours = duration.total_seconds() / 3600
        charged_hours = math.ceil(total_hours) if total_hours > 0 else 1
        fee = charged_hours * 10000
        self.info_vars["Biển Số Xe:"].set(db_plate)
        self.info_vars["ID Thẻ:"].set(db_rfid)
        self.info_vars["Trạng Thái:"].set("Xe ra")
        self.info_vars["Thời gian:"].set(exit_time.strftime('%Y-%m-%d %H:%M:%S'))
        self.info_vars["Tổng giờ gửi:"].set(duration_str.strip())
        self.info_vars["Tổng Phí:"].set(f"{int(fee):,} VNĐ")
        self.info_vars["Trạng Thái Thanh Toán:"].set("Chưa thanh toán")
        self.btn_car_entry.config(state=tk.DISABLED)
        self.btn_car_exit.config(state=tk.DISABLED)
        self.mqtt_client.publish("parking/command", "PLATE_OK_OUT")
        vehicle_data_dict = {
            'license_plate': db_plate,
            'hours': charged_hours
        }
        def on_payment_success(transaction_data):
            logging.info(f"Thanh toán thành công: {transaction_data}")
            image_name = f"RA_{db_plate.replace('.', '')}_{exit_time.strftime('%Y%m%d%H%M%S')}.jpg"
            image_path = os.path.join('anh', image_name)
            if self.current_frame_out is not None:
                cv2.imwrite(image_path, self.current_frame_out)
            self.db.log_car_exit(self.active_vehicle_id, exit_time, fee, image_path, self.current_user['name'])
            self.info_vars["Trạng Thái:"].set("Đã rời bãi")
            self.info_vars["Trạng Thái Thanh Toán:"].set("Đã thanh toán")
            self.btn_car_entry.config(state=tk.NORMAL)
            self.btn_car_exit.config(state=tk.NORMAL)
            messagebox.showinfo("Thành công", f"Xe {db_plate} đã ra khỏi bãi.\nPhí: {fee:,} VNĐ")
            self.mqtt_client.publish("parking/command", f"PAYMENT_SUCCESS:{fee}")
            self.root.after(2000, self.reset_info_panel)
        def on_payment_timeout():
            logging.warning("Thời gian chờ thanh toán hết hạn")
            messagebox.showwarning("Hết thời gian", "Thời gian chờ thanh toán đã hết. Vui lòng thử lại.")
            self.mqtt_client.publish("parking/command", "PAYMENT_FAIL")
            self.btn_car_entry.config(state=tk.NORMAL)
            self.btn_car_exit.config(state=tk.NORMAL)
            self.reset_info_panel()
        payment_data = self.payment_manager.start_payment_flow(vehicle_data_dict, fee, on_payment_success, on_payment_timeout)
        self.show_qr_payment(payment_data['qr_url'], payment_data['session_id'], payment_data['amount'], payment_data['description'], on_payment_success)
    def show_qr_payment(self, qr_url, session_id, amount, description, on_payment_success):
        qr_window = tk.Toplevel(self.root)
        qr_window.title("Thanh toán qua QR")
        qr_window.geometry('400x500')
        qr_window.transient(self.root)
        qr_window.grab_set()
        qr_canvas = tk.Canvas(qr_window, width=300, height=300)
        qr_canvas.pack(pady=10)
        try:
            with urllib.request.urlopen(qr_url) as response:
                qr_img = Image.open(response).resize((300, 300), Image.Resampling.LANCZOS)
                qr_photo = ImageTk.PhotoImage(qr_img)
                qr_canvas.create_image(0, 0, image=qr_photo, anchor='nw')
                qr_canvas.image = qr_photo
        except Exception as e:
            logging.error(f"Lỗi tải mã QR: {e}")
            qr_canvas.create_text(150, 150, text="Lỗi tải mã QR", fill="red", font=("Arial", 14))
        ttk.Label(qr_window, text=f"Số tiền: {amount:,} VNĐ").pack(pady=5)
        ttk.Label(qr_window, text=f"Nội dung: {description}").pack(pady=5)
        ttk.Button(qr_window, text="Thu tiền mặt", command=lambda: [self.payment_manager.cancel_payment(session_id), self.mqtt_client.publish("parking/command", f"PAYMENT_SUCCESS:{amount}"), on_payment_success({}), qr_window.destroy()]).pack(pady=10)
    def show_history(self):
        self.current_screen = "history"
        self.clear_content()
        self.show_user_menu()
        self.release_cameras()
        frame = ttk.Frame(self.content_frame)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        filter_frame = ttk.LabelFrame(frame, text="Bộ lọc")
        filter_frame.pack(fill=tk.X, pady=5)
        ttk.Label(filter_frame, text="Biển số:").pack(side=tk.LEFT, padx=5)
        self.hist_plate_entry = ttk.Entry(filter_frame)
        self.hist_plate_entry.pack(side=tk.LEFT, padx=5)
        ttk.Label(filter_frame, text="Ngày vào:").pack(side=tk.LEFT, padx=5)
        self.hist_date_entry = DateEntry(filter_frame, date_pattern='y-mm-dd')
        self.hist_date_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(filter_frame, text="Lọc", command=self.load_history_data).pack(side=tk.LEFT, padx=5)
        ttk.Button(filter_frame, text="Xóa Lọc", command=self.clear_history_filter).pack(side=tk.LEFT, padx=5)
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        cols = ('ID', 'Biển số', 'RFID', 'Vào', 'Ra', 'Phí', 'Trạng thái', 'Payment status', 'NV Vào', 'NV Ra')
        self.history_tree = ttk.Treeview(tree_frame, columns=cols, show='headings')
        for col in cols:
            self.history_tree.heading(col, text=col)
            self.history_tree.column(col, stretch=tk.YES)
        self.history_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.history_tree.yview)
        self.history_tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        ttk.Button(frame, text="Xóa mục đã chọn", style='Danger.TButton', command=self.delete_history_record).pack(pady=10)
        self.load_history_data()
    def load_history_data(self):
        for item in self.history_tree.get_children(): self.history_tree.delete(item)
        plate = self.hist_plate_entry.get()
        date_val = self.hist_date_entry.get()
        date = self.hist_date_entry.get_date().strftime('%Y-%m-%d') if date_val else None
        data = self.db.get_history(plate, date)
        for row in data: self.history_tree.insert('', tk.END, values=row)
    def clear_history_filter(self):
        self.hist_plate_entry.delete(0, tk.END)
        self.hist_date_entry.set_date(None)
        self.load_history_data()
    def delete_history_record(self):
        selected_item = self.history_tree.focus()
        if not selected_item:
            messagebox.showwarning("Cảnh báo", "Vui lòng chọn một mục để xóa.")
            return
        item_data = self.history_tree.item(selected_item)
        record_id = item_data['values'][0]
        if messagebox.askyesno("Xác nhận", f"Bạn có chắc chắn muốn xóa bản ghi ID {record_id}?"):
            self.db.delete_history(record_id)
            self.load_history_data()
    def show_staff_management(self):
        self.current_screen = "staff"
        self.clear_content()
        self.show_user_menu()
        self.release_cameras()
        frame = ttk.LabelFrame(self.content_frame, text="Quản lý nhân sự")
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        cols = ('ID', 'Username', 'Full name', 'Role')
        self.staff_tree = ttk.Treeview(tree_frame, columns=cols, show='headings')
        for col in cols:
            self.staff_tree.heading(col, text=col)
            self.staff_tree.column(col, stretch=tk.YES)
        self.staff_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.staff_tree.yview)
        self.staff_tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Add", command=self.add_staff).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Edit", command=self.edit_staff).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Delete", command=self.delete_staff).pack(side=tk.LEFT, padx=5)
        self.load_staff_data()
    def load_staff_data(self):
        for item in self.staff_tree.get_children(): self.staff_tree.delete(item)
        for user in self.db.get_users(): self.staff_tree.insert('', tk.END, values=user)
    def _staff_dialog(self, title, record=None):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="Username:").grid(row=0, column=0, padx=5, pady=5)
        user_entry = ttk.Entry(dialog)
        user_entry.grid(row=0, column=1, padx=5, pady=5)
        ttk.Label(dialog, text="Password:").grid(row=1, column=0, padx=5, pady=5)
        pass_entry = ttk.Entry(dialog, show="*")
        pass_entry.grid(row=1, column=1, padx=5, pady=5)
        if record: ttk.Label(dialog, text="(Leave blank if no change)").grid(row=1, column=2)
        ttk.Label(dialog, text="Full name:").grid(row=2, column=0, padx=5, pady=5)
        name_entry = ttk.Entry(dialog)
        name_entry.grid(row=2, column=1, padx=5, pady=5)
        ttk.Label(dialog, text="Role:").grid(row=3, column=0, padx=5, pady=5)
        role_combo = ttk.Combobox(dialog, values=['admin', 'user'], state='readonly')
        role_combo.grid(row=3, column=1, padx=5, pady=5)
        if record:
            user_entry.insert(0, record[1])
            name_entry.insert(0, record[2])
            role_combo.set(record[3])
        result = {}
        def on_ok():
            result['username'] = user_entry.get()
            result['password'] = pass_entry.get()
            result['full_name'] = name_entry.get()
            result['role'] = role_combo.get()
            dialog.destroy()
        ok_btn = ttk.Button(dialog, text="OK", command=on_ok)
        ok_btn.grid(row=4, column=0, columnspan=2, pady=10)
        dialog.wait_window(dialog)
        return result
    def add_staff(self):
        data = self._staff_dialog("Thêm nhân viên")
        if data and all(data.values()):
            if self.db.add_user(**data): self.load_staff_data()
            else: messagebox.showerror("Lỗi", "Tên đăng nhập đã tồn tại.")
        elif data: messagebox.showwarning("Cảnh báo", "Vui lòng điền đủ thông tin.")
    def edit_staff(self):
        selected_item = self.staff_tree.focus()
        if not selected_item: return
        record = self.staff_tree.item(selected_item)['values']
        user_id = record[0]
        data = self._staff_dialog("Sửa nhân viên", record)
        if data and data['username'] and data['full_name'] and data['role']:
            if self.db.update_user(user_id, **data): self.load_staff_data()
            else: messagebox.showerror("Lỗi", "Tên đăng nhập đã tồn tại.")
        elif data: messagebox.showwarning("Cảnh báo", "Tên, họ tên và vai trò không được để trống.")
    def delete_staff(self):
        selected_item = self.staff_tree.focus()
        if not selected_item: return
        record = self.staff_tree.item(selected_item)['values']
        user_id, username = record[0], record[1]
        if username == self.current_user['name'] or username == 'admin':
            messagebox.showerror("Lỗi", "Không thể xóa tài khoản admin hoặc chính bạn.")
            return
        if messagebox.askyesno("Xác nhận", f"Bạn có chắc muốn xóa nhân viên {username}?"):
            self.db.delete_user(user_id)
            self.load_staff_data()
    def show_revenue_report(self):
        self.current_screen = "revenue"
        self.clear_content()
        self.show_user_menu()
        self.release_cameras()
        frame = ttk.LabelFrame(self.content_frame, text="Báo cáo doanh thu")
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        filter_frame = ttk.Frame(frame)
        filter_frame.pack(pady=20)
        ttk.Label(filter_frame, text="Từ ngày:").pack(side=tk.LEFT, padx=5)
        self.start_date_entry = DateEntry(filter_frame, date_pattern='y-mm-dd')
        self.start_date_entry.pack(side=tk.LEFT, padx=5)
        ttk.Label(filter_frame, text="Đến ngày:").pack(side=tk.LEFT, padx=5)
        self.end_date_entry = DateEntry(filter_frame, date_pattern='y-mm-dd')
        self.end_date_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(filter_frame, text="Xem Báo Cáo", command=self.generate_revenue_report).pack(side=tk.LEFT, padx=10)
        self.revenue_var = tk.StringVar(value="Tổng doanh thu: 0 VNĐ")
        ttk.Label(frame, textvariable=self.revenue_var, font=('Arial', 24, 'bold'), foreground=self.colors['danger']).pack(pady=30)
    def generate_revenue_report(self):
        start_date = self.start_date_entry.get_date().strftime('%Y-%m-%d')
        end_date = self.end_date_entry.get_date().strftime('%Y-%m-%d')
        total = self.db.get_revenue_report(start_date, end_date)
        self.revenue_var.set(f"Tổng doanh thu: {total:,} VNĐ")