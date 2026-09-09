
from paddleocr import PaddleOCR
import os
os.environ.setdefault("PADDLE_PDX_CACHE_HOME","/tmp/paddlex")
print("=== Preloading PaddleOCR models ===")
PaddleOCR(text_detection_model_name="PP-OCRv6_tiny_det",
          text_recognition_model_name="PP-OCRv6_small_rec")
PaddleOCR(text_detection_model_name="PP-OCRv6_small_det",
          text_recognition_model_name="PP-OCRv6_small_rec")
print("=== Preload complete ===")
