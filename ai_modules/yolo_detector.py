from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

import config
from utils.settings_store import get_settings


logger = logging.getLogger(__name__)

try:
    from ultralytics import YOLO
except Exception:  # pragma: no cover - import guard for environments without Ultralytics
    YOLO = None

_CACHED_YOLO_MODEL = None
_YOLO_INIT_LOCK = threading.Lock()


class YoloDetector:
    PHONE_CLASS_ALIASES = {
        "cell phone",
        "cellphone",
        "phone",
        "mobile phone",
        "mobilephone",
        "smartphone",
    }

    @staticmethod
    def normalize_class_name(class_name: str) -> str:
        normalized = str(class_name or "").strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return normalized.strip()

    @classmethod
    def is_phone_class_name(cls, class_name: str) -> bool:
        normalized = cls.normalize_class_name(class_name)
        if not normalized:
            return False
        if normalized in cls.PHONE_CLASS_ALIASES:
            return True
        return any(alias in normalized for alias in ("cell phone", "cellphone", "mobile phone", "mobilephone", "smartphone", "phone"))

    def __init__(self, model_name: str = "yolov8n.pt"):
        global _CACHED_YOLO_MODEL
        self.model = None
        self._lock = threading.Lock()

        if YOLO is None:
            logger.warning("Ultralytics is unavailable; YOLO detection disabled.")
            return

        with _YOLO_INIT_LOCK:
            if _CACHED_YOLO_MODEL is not None:
                self.model = _CACHED_YOLO_MODEL
                return

            try:
                # Look in project root or relative path
                model_path = model_name
                if not Path(model_path).exists():
                    root_model = Path(__file__).resolve().parents[1] / model_name
                    if root_model.exists():
                        model_path = str(root_model)

                logger.info("Loading YOLO model from %s...", model_path)
                loaded = YOLO(model_path)
                _CACHED_YOLO_MODEL = loaded
                self.model = loaded
                logger.info("YOLO model loaded and cached successfully.")
            except Exception as error:
                logger.warning("YOLO model failed to load: %s", error)

    def warmup(self) -> None:
        """Run a lightweight inference on dummy frame to warm up PyTorch/CUDA/CPU graph."""
        if self.model is None:
            return
        try:
            dummy = np.zeros((320, 320, 3), dtype=np.uint8)
            with self._lock:
                self.model(dummy, conf=0.5, verbose=False)
        except Exception:
            pass

    def detect(self, frame) -> List[Dict[str, Any]]:
        if frame is None or self.model is None:
            return []

        confidence_threshold = get_settings().get("YOLO_CONFIDENCE", 0.50)
        try:
            with self._lock:
                results = self.model(frame, conf=confidence_threshold, verbose=False)
        except Exception as error:
            logger.warning("YOLO frame detection failed: %s", error)
            return []

        detections: List[Dict[str, Any]] = []
        for result in results:
            names = result.names
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue

            for box in boxes:
                confidence = float(box.conf[0])
                if confidence < confidence_threshold:
                    continue

                class_id = int(box.cls[0])
                raw_class_name = names.get(class_id, str(class_id))
                normalized_class_name = self.normalize_class_name(raw_class_name)
                coordinates = box.xyxy[0].tolist()
                detections.append(
                    {
                        "class_name": normalized_class_name,
                        "class_name_raw": raw_class_name,
                        "confidence": confidence,
                        "bbox": {
                            "x": int(coordinates[0]),
                            "y": int(coordinates[1]),
                            "width": max(int(coordinates[2] - coordinates[0]), 0),
                            "height": max(int(coordinates[3] - coordinates[1]), 0),
                        },
                    }
                )

        logger.debug(
            "YOLO detections threshold=%.2f raw_count=%d classes=%s",
            confidence_threshold,
            len(detections),
            [d.get("class_name") for d in detections],
        )
        return detections

    def is_available(self) -> bool:
        return self.model is not None

    def get_status(self) -> str:
        return "Active (YOLOv8n)" if self.model is not None else "Unavailable"
