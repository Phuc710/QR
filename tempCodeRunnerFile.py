import tkinter as tk
from database import Database
from main_app import MainApplication
from login_flow import LoginFlow
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    root = tk.Tk()
    root.withdraw()

    try:
        db = Database()
    except Exception as e:
        logging.error(f"Không thể kết nối tới database. Lỗi: {e}")
        root.destroy()
        return
    main_app = MainApplication(root, db)

    def on_login_success(user_info):
        logging.info(f"Đăng nhập thành công với user: {user_info['name']}")
        if login_manager.login_window:
            login_manager.login_window.destroy()
        main_app.start(user_info)

    login_manager = LoginFlow(
        root=root,
        login_check_callback=db.check_user,
        on_success_callback=on_login_success
    )

    def loading_task(update_progress_callback):
        main_app.load_models(update_progress_callback)

    login_manager.start(loading_task)

    root.mainloop()

if __name__ == "__main__":
    main()