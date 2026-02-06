import sys
import threading
import sounddevice as sd
import numpy as np
import base64
import pyperclip
import json
import os
import requests
import ctypes  # Библиотека для работы с функциями C (ядро Windows)
from io import BytesIO
from PIL import ImageGrab
from faster_whisper import WhisperModel

from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow, QTabWidget,
                             QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
                             QLineEdit, QPushButton)
from PyQt6.QtCore import QTimer, pyqtSignal, QObject, Qt

CONFIG_FILE = "settings_win.json"
DEFAULT_PROMPT = "Ты — Senior QA. Отвечай кратко и четко."

# Константы Windows API
# WDA_EXCLUDEFROMCAPTURE делает окно невидимым для любого захвата экрана
WDA_EXCLUDEFROMCAPTURE = 0x00000011


# ================= SAFE QT SIGNALS =================
class SafeSignals(QObject):
    log = pyqtSignal(str)
    text = pyqtSignal(str)
    status = pyqtSignal(str)
    btn_auto_text = pyqtSignal(str)


# ================= MAIN WINDOW =================
class InterviewAssistantWin(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stealth Assistant PRO")
        self.resize(460, 700)

        # Флаг StaysOnTopHint — окно всегда будет поверх остальных
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        self.signals = SafeSignals()
        self.signals.log.connect(self._add_log)
        self.signals.text.connect(self._set_text)
        self.signals.status.connect(self._set_status)
        self.signals.btn_auto_text.connect(self._set_btn_auto_text)

        self.audio_frames = []
        self.is_recording = False
        self.auto_mode = False
        self.whisper_model = None
        self.fs = 16000

        self.auto_timer = QTimer()
        self.auto_timer.timeout.connect(self.auto_process_cycle)
        self.countdown_timer = QTimer()
        self.countdown_timer.timeout.connect(self.update_countdown)
        self.auto_seconds_left = 0

        self.init_ui()
        self.load_settings()

        # Запускаем активацию стелса через 200мс после инициализации,
        # чтобы Windows успела зарегистрировать окно в системе.
        QTimer.singleShot(200, self.apply_hard_stealth)

    # ================= HARD STEALTH (WINAPI) =================
    def apply_hard_stealth(self):
        """Прямое обращение к ядру Windows для скрытия окна."""
        try:
            # winId() возвращает уникальный номер (Handle) окна в Windows
            hwnd = int(self.winId())

            # Обращаемся к функции SetWindowDisplayAffinity из user32.dll
            # Аргументы: (ID окна, Флаг скрытия)
            result = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

            if result:
                self.signals.log.emit("🛡 STEALTH: АКТИВИРОВАН (WinAPI)")
            else:
                self.signals.log.emit("❌ STEALTH: Ошибка активации системы")
        except Exception as e:
            self.signals.log.emit(f"⚠️ Ошибка WinAPI: {e}")

    # ================= UI =================
    def init_ui(self):
        tabs = QTabWidget(self)
        self.setCentralWidget(tabs)

        chat = QWidget()
        layout = QVBoxLayout()

        self.status_label = QLabel("⚪ ГОТОВ")
        layout.addWidget(self.status_label)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)

        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setFixedHeight(80)
        self.log_widget.setStyleSheet("background: #1e1e1e; color: #00ff00; font-family: 'Courier New';")
        layout.addWidget(self.log_widget)

        row = QHBoxLayout()
        self.btn_mic = QPushButton("🎙 МИК")
        self.btn_mic.setCheckable(True)
        self.btn_mic.clicked.connect(self.toggle_mic)
        row.addWidget(self.btn_mic)

        self.btn_auto = QPushButton("🤖 АВТО")
        self.btn_auto.setCheckable(True)
        self.btn_auto.clicked.connect(self.toggle_auto_mode)
        row.addWidget(self.btn_auto)

        btn_scr = QPushButton("📸 SCREEN")
        btn_scr.clicked.connect(self.take_screenshot)
        row.addWidget(btn_scr)
        layout.addLayout(row)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Введите текст...")
        self.input.returnPressed.connect(self.send_text)
        layout.addWidget(self.input)

        chat.setLayout(layout)
        tabs.addTab(chat, "Чат")

        settings = QWidget()
        s_layout = QVBoxLayout()
        s_layout.addWidget(QLabel("OpenRouter API Key"))
        self.token_input = QLineEdit()
        s_layout.addWidget(self.token_input)
        s_layout.addWidget(QLabel("Whisper Model"))
        self.whisper_input = QLineEdit("base")
        s_layout.addWidget(self.whisper_input)
        s_layout.addWidget(QLabel("Prompt"))
        self.prompt_edit = QTextEdit(DEFAULT_PROMPT)
        s_layout.addWidget(self.prompt_edit)
        btn_save = QPushButton("💾 Сохранить")
        btn_save.clicked.connect(self.save_settings)
        s_layout.addWidget(btn_save)
        settings.setLayout(s_layout)
        tabs.addTab(settings, "⚙️")

    # ================= LOGIC =================
    def _add_log(self, t):
        self.log_widget.append(t)

    def _set_status(self, t):
        self.status_label.setText(t)

    def _set_btn_auto_text(self, t):
        self.btn_auto.setText(t)

    def _set_text(self, t):
        self.output.setHtml(t.replace("\n", "<br>"))
        pyperclip.copy(t)

    def save_settings(self):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "token": self.token_input.text(),
                "prompt": self.prompt_edit.toPlainText(),
                "whisper": self.whisper_input.text()
            }, f, ensure_ascii=False)
        self.signals.log.emit("✅ Настройки сохранены")

    def load_settings(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                self.token_input.setText(d.get("token", ""))
                self.prompt_edit.setText(d.get("prompt", DEFAULT_PROMPT))
                self.whisper_input.setText(d.get("whisper", "base"))

    def toggle_mic(self):
        if self.auto_mode: self.stop_auto()
        if not self.is_recording:
            self.is_recording = True
            self.audio_frames = []
            self.signals.status.emit("🔴 ЗАПИСЬ")
            threading.Thread(target=self.record_loop, daemon=True).start()
        else:
            self.is_recording = False
            self.signals.status.emit("⌛ ЖДИ")
            threading.Thread(target=self.process_audio, daemon=True).start()

    def toggle_auto_mode(self):
        if self.is_recording: self.is_recording = False
        if self.btn_auto.isChecked():
            self.auto_mode = True
            self.audio_frames = []
            self.auto_seconds_left = 15
            self.signals.status.emit("▶️ АВТО")
            threading.Thread(target=self.record_loop, daemon=True).start()
            self.auto_timer.start(15000)
            self.countdown_timer.start(1000)
        else:
            self.stop_auto()

    def stop_auto(self):
        self.auto_mode = False
        self.auto_timer.stop()
        self.countdown_timer.stop()
        self.btn_auto.setChecked(False)
        self.signals.btn_auto_text.emit("🤖 АВТО")
        self.signals.status.emit("⚪ ГОТОВ")

    def update_countdown(self):
        self.auto_seconds_left -= 1
        if self.auto_seconds_left < 0: self.auto_seconds_left = 14
        self.signals.btn_auto_text.emit(f"🤖 АВТО ({self.auto_seconds_left}s)")

    def auto_process_cycle(self):
        if self.audio_frames:
            batch = list(self.audio_frames)
            self.audio_frames = []
            threading.Thread(target=self.process_audio_silent, args=(batch,), daemon=True).start()

    def record_loop(self):
        try:
            def cb(indata, frames, time, status):
                if self.is_recording or self.auto_mode:
                    self.audio_frames.append(indata.copy())

            with sd.InputStream(samplerate=self.fs, channels=1, callback=cb):
                while self.is_recording or self.auto_mode:
                    sd.sleep(100)
        except Exception as e:
            self.signals.log.emit(f"Audio Error: {e}")

    def process_audio(self):
        text = self.run_whisper(self.audio_frames)
        if text:
            self.signals.log.emit(f"🎤 {text}")
            self.ask_ai(text)
        self.signals.status.emit("⚪ ГОТОВ")
        self.btn_mic.setChecked(False)

    def process_audio_silent(self, frames):
        text = self.run_whisper(frames)
        if text:
            self.signals.log.emit(f"🤖 Auto: {text[:30]}...")
            self.ask_ai(text)

    def run_whisper(self, frames):
        try:
            if not frames: return None
            audio = np.concatenate(frames).flatten()
            if not self.whisper_model:
                self.whisper_model = WhisperModel(self.whisper_input.text(), device="cpu", compute_type="int8")
            segments, _ = self.whisper_model.transcribe(audio, language="ru")
            return " ".join([s.text for s in segments]).strip()
        except Exception as e:
            self.signals.log.emit(f"Whisper error: {e}")
            return None

    def ask_ai(self, text, image_b64=None):
        token = self.token_input.text().strip()
        if not token: return
        content = [{"type": "text", "text": text}]
        if image_b64:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}})
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "model": "google/gemini-2.0-flash-001",
                    "messages": [{"role": "system", "content": self.prompt_edit.toPlainText()},
                                 {"role": "user", "content": content}]
                },
                timeout=60
            )
            if r.status_code == 200:
                self.signals.text.emit(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            self.signals.log.emit(f"AI Error: {e}")

    def send_text(self):
        t = self.input.text()
        if t:
            self.input.clear()
            threading.Thread(target=self.ask_ai, args=(t,), daemon=True).start()

    def take_screenshot(self):
        try:
            img = ImageGrab.grab()
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=70)
            img_str = base64.b64encode(buf.getvalue()).decode('utf-8')
            threading.Thread(target=self.ask_ai, args=("Ответь по экрану", img_str), daemon=True).start()
        except Exception as e:
            self.signals.log.emit(f"Screen Error: {e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = InterviewAssistantWin()
    win.show()
    sys.exit(app.exec())