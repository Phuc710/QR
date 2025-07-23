from PIL import Image
import cv2
import torch
import os
import time
import threading
import numpy as np
import logging
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

try:
    import function.utils_rotate as utils_rotate
except ImportError:
    utils_rotate = None

try:
    import function.helper as helper
except ImportError:
    helper = None

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

class OptimizedLPR:
    LP_DETECTOR_MODEL_PATH = 'model/LP_detector_nano_61.pt'
    OCR_MODEL_PATH = 'model/LP_ocr_nano_62.pt'
    DEFAULT_DETECTOR_CONF = 0.4
    DEFAULT_OCR_CONF = 0.5
    MAX_FRAME_WIDTH_RESIZE = 1280
    MIN_PLATE_WIDTH_OCR = 100
    PLATE_CROP_PADDING = 5
    MIN_AREA_THRESHOLD = 1000
    CACHE_TIMEOUT = 2.0

    def __init__(self):
        self.yolo_LP_detect = None
        self.yolo_license_plate = None
        self.processing_lock = threading.Lock()
        self.models_loaded = False
        self.plate_cache = {}

    def load_models(self) -> bool:
        if self.models_loaded:
            return True

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                
                device = 'cuda' if torch.cuda.is_available() else 'cpu'
                
                if os.path.exists(self.LP_DETECTOR_MODEL_PATH):
                    self.yolo_LP_detect = torch.hub.load(
                        'ultralytics/yolov5', 'custom',
                        path=self.LP_DETECTOR_MODEL_PATH,
                        force_reload=False,
                        device=device,
                        trust_repo=True
                    )
                    self.yolo_LP_detect.conf = self.DEFAULT_DETECTOR_CONF
                else:
                    self.yolo_LP_detect = torch.hub.load('ultralytics/yolov5', 'yolov5s', device=device, trust_repo=True)
                    self.yolo_LP_detect.conf = 0.3

                if os.path.exists(self.OCR_MODEL_PATH):
                    self.yolo_license_plate = torch.hub.load(
                        'ultralytics/yolov5', 'custom',
                        path=self.OCR_MODEL_PATH,
                        force_reload=False,
                        device=device,
                        trust_repo=True
                    )
                    self.yolo_license_plate.conf = self.DEFAULT_OCR_CONF
                
                dummy_frame = np.zeros((640, 640, 3), dtype=np.uint8)
                _ = self.yolo_LP_detect(dummy_frame, size=640)

            self.models_loaded = True
            return True

        except Exception as e:
            logging.error(f"Failed to load models: {e}")
            return False

    def preprocess_frame(self, frame: np.ndarray) -> np.ndarray:
        if frame is None or frame.size == 0:
            return frame

        try:
            height, width = frame.shape[:2]
            
            if width > self.MAX_FRAME_WIDTH_RESIZE:
                scale = self.MAX_FRAME_WIDTH_RESIZE / width
                new_width = self.MAX_FRAME_WIDTH_RESIZE
                new_height = int(height * scale)
                frame = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)

            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            l = clahe.apply(l)
            enhanced_frame = cv2.merge([l, a, b])
            enhanced_frame = cv2.cvtColor(enhanced_frame, cv2.COLOR_LAB2BGR)

            return enhanced_frame
        except Exception as e:
            logging.error(f"Error during frame preprocessing: {e}")
            return frame

    def detect_and_read_plate(self, frame: np.ndarray) -> dict:
        if not self.models_loaded:
            return {'success': False, 'plates': [], 'error': "Models not loaded"}

        if frame is None or frame.size == 0:
            return {'success': False, 'plates': [], 'error': "Input frame is empty"}

        with self.processing_lock:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    
                    processed_frame = self.preprocess_frame(frame)
                    frame_hash = hash(processed_frame.tobytes())

                    plates_data = self.yolo_LP_detect(processed_frame, size=640)
                    detections = plates_data.xyxy[0].cpu().numpy()
                
                if detections.size == 0:
                    return {'success': False, 'plates': [], 'error': "No license plates detected"}

                detected_plates = []
                plates_with_area = [(plate, (plate[2] - plate[0]) * (plate[3] - plate[1])) 
                                  for plate in detections 
                                  if (plate[2] - plate[0]) * (plate[3] - plate[1]) > self.MIN_AREA_THRESHOLD]
                
                plates_with_area.sort(key=lambda x: x[1], reverse=True)
                
                for plate, area in plates_with_area[:2]:
                    x1, y1, x2, y2, conf, cls = plate
                    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

                    if x2 <= x1 or y2 <= y1:
                        continue

                    cache_key = f"{frame_hash}_{x1}_{y1}_{x2}_{y2}"
                    if cache_key in self.plate_cache:
                        cached_result, timestamp = self.plate_cache[cache_key]
                        if time.time() - timestamp < self.CACHE_TIMEOUT:
                            detected_plates.append({
                                'bbox': (x1, y1, x2, y2),
                                'text': cached_result,
                                'confidence': float(conf),
                                'cached': True
                            })
                            continue

                    x1_crop = max(0, x1 - self.PLATE_CROP_PADDING)
                    y1_crop = max(0, y1 - self.PLATE_CROP_PADDING)
                    x2_crop = min(processed_frame.shape[1], x2 + self.PLATE_CROP_PADDING)
                    y2_crop = min(processed_frame.shape[0], y2 + self.PLATE_CROP_PADDING)

                    crop_img = processed_frame[y1_crop:y2_crop, x1_crop:x2_crop]

                    if crop_img.size == 0:
                        continue

                    plate_text = self.read_plate_optimized(crop_img)

                    if plate_text and plate_text != "unknown" and len(plate_text) > 3:
                        self.plate_cache[cache_key] = (plate_text, time.time())
                        detected_plates.append({
                            'bbox': (x1, y1, x2, y2),
                            'text': plate_text,
                            'confidence': float(conf),
                            'cropped_image': crop_img,
                            'cached': False
                        })

                detected_plates.sort(key=lambda x: x['confidence'], reverse=True)
                return {'success': len(detected_plates) > 0, 'plates': detected_plates, 'error': None}

            except Exception as e:
                logging.error(f"Error during detection: {e}")
                return {'success': False, 'plates': [], 'error': str(e)}

    def read_plate_optimized(self, crop_img: np.ndarray) -> str:
        if crop_img is None or crop_img.size == 0:
            return "unknown"

        try:
            if self.yolo_license_plate and helper:
                height, width = crop_img.shape[:2]
                if width < self.MIN_PLATE_WIDTH_OCR:
                    scale = self.MIN_PLATE_WIDTH_OCR / width
                    new_width = self.MIN_PLATE_WIDTH_OCR
                    new_height = int(height * scale)
                    crop_img = cv2.resize(crop_img, (new_width, new_height), interpolation=cv2.INTER_LINEAR)

                plate_text = helper.read_plate(self.yolo_license_plate, crop_img)
                if plate_text and plate_text != "unknown" and len(plate_text) > 3:
                    return plate_text

            return self.tesseract_ocr(crop_img)

        except Exception as e:
            logging.error(f"Error in OCR: {e}")
            return "unknown"

    def tesseract_ocr(self, crop_img: np.ndarray) -> str:
        if not TESSERACT_AVAILABLE or crop_img is None or crop_img.size == 0:
            return "unknown"

        try:
            gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            
            custom_config = r'--oem 3 --psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
            text = pytesseract.image_to_string(thresh, config=custom_config).strip()
            
            if len(text) >= 4 and text.replace(' ', '').isalnum():
                return text.replace(' ', '').upper()
            
            return "unknown"

        except Exception:
            return "unknown"

    def process_image_file(self, image_path: str) -> dict:
        if not os.path.exists(image_path):
            return {'success': False, 'plates': [], 'error': f"Image file not found: {image_path}"}

        frame = cv2.imread(image_path)
        if frame is None:
            return {'success': False, 'plates': [], 'error': f"Could not load image: {image_path}"}

        return self.detect_and_read_plate(frame)

    def get_best_plate(self, detection_result: dict) -> dict | None:
        if not detection_result['success'] or not detection_result['plates']:
            return None
        return detection_result['plates'][0]

    def is_ready(self) -> bool:
        return self.models_loaded

    def clear_cache(self):
        self.plate_cache.clear()