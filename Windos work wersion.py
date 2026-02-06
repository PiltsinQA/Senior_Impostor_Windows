import sys
import threading
import queue
import json
import os
import requests
import base64
import ctypes
import re
import numpy as np
import pyaudiowpatch as pyaudio  # Библиотека для захвата любого звука в Windows
import pyperclip
from io import BytesIO
from PIL import ImageGrab
from faster_whisper import WhisperModel

from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow, QTabWidget,
                             QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
                             QLineEdit, QPushButton)
from PyQt6.QtCore import QTimer, pyqtSignal, QObject, Qt

# Константы для Stealth-режима (скрытие от записи экрана)
WDA_EXCLUDEFROMCAPTURE = 0x00000011
CONFIG_FILE = "settings_win.json"
DEFAULT_PROMPT = "Ты — Senior QA. Отвечай кратко и четко."


# ================= SAFE QT SIGNALS =================
class SafeSignals(QObject):
    """Класс для передачи данных между потоком аудио и GUI (интерфейсом)"""
    log = pyqtSignal(str)
    text = pyqtSignal(str)
    status = pyqtSignal(str)
    btn_auto_text = pyqtSignal(str)


# ================= MAIN WINDOW =================
class InterviewAssistantWin(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stealth Assistant PRO v5.1")
        self.resize(460, 750)

        # Флаг "Поверх всех окон"
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        self.signals = SafeSignals()
        self.signals.log.connect(self._add_log)
        self.signals.text.connect(self._set_text)
        self.signals.status.connect(self._set_status)
        self.signals.btn_auto_text.connect(self._set_btn_auto_text)

        # Переменные управления
        self.is_running = False  # Работает ли захват звука вообще
        self.auto_mode = False  # Режим цикличной отправки (Интервьюер)
        self.mic_mode = False  # Режим записи микрофона (Вы)
        self.whisper_model = None
        self.accumulated_text = ""  # Буфер текста для АВТО режима

        # Таймеры для АВТО-режима
        self.auto_timer = QTimer()
        self.auto_timer.timeout.connect(self.trigger_ai_send)
        self.countdown_timer = QTimer()
        self.countdown_timer.timeout.connect(self.update_countdown)
        self.auto_seconds_left = 0

        self.init_ui()
        self.load_settings()

        # Активация невидимости окна через WinAPI
        QTimer.singleShot(500, self.apply_hard_stealth)

    # ---------------- STEALTH ----------------
    def apply_hard_stealth(self):
        try:
            hwnd = int(self.winId())
            # Функция ядра Windows, исключающая окно из захвата (Affinity)
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            self.signals.log.emit("🛡 STEALTH: АКТИВИРОВАН")
        except:
            self.signals.log.emit("❌ STEALTH: Ошибка")

    # ---------------- UI ----------------
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
        self.log_widget.setStyleSheet(
            "background: #1e1e1e; color: #00ff00; font-family: 'Courier New'; font-size: 10px;")
        layout.addWidget(self.log_widget)

        # Ряд кнопок управления
        row = QHBoxLayout()

        # Кнопка МИК (Ваш голос)
        self.btn_mic = QPushButton("🎙 МИК")
        self.btn_mic.setCheckable(True)
        self.btn_mic.setFixedHeight(40)
        self.btn_mic.clicked.connect(self.toggle_mic_mode)
        self.btn_mic.setStyleSheet("background-color: #2980b9; color: white;")
        row.addWidget(self.btn_mic)

        # Кнопка АВТО (Голос собеседника из системы)
        self.btn_auto = QPushButton("🤖 АВТО")
        self.btn_auto.setCheckable(True)
        self.btn_auto.setFixedHeight(40)
        self.btn_auto.clicked.connect(self.toggle_auto_mode)
        self.btn_auto.setStyleSheet("background-color: #27ae60; color: white;")
        row.addWidget(self.btn_auto)

        btn_scr = QPushButton("📸 SCR")
        btn_scr.setFixedHeight(40)
        btn_scr.clicked.connect(self.take_screenshot)
        row.addWidget(btn_scr)
        layout.addLayout(row)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Текст вручную...")
        self.input.returnPressed.connect(self.send_manual_text)
        layout.addWidget(self.input)

        chat.setLayout(layout)
        tabs.addTab(chat, "Чат")

        # Вкладка настроек
        settings = QWidget()
        s_layout = QVBoxLayout()
        s_layout.addWidget(QLabel("API Key (OpenRouter)"))
        self.token_input = QLineEdit()
        s_layout.addWidget(self.token_input)

        s_layout.addWidget(QLabel("Whisper Model (tiny/base/small)"))
        self.whisper_input = QLineEdit("base")
        s_layout.addWidget(self.whisper_input)

        s_layout.addWidget(QLabel("Интервал АВТО (сек)"))
        self.auto_interval_input = QLineEdit("15")
        s_layout.addWidget(self.auto_interval_input)

        self.prompt_edit = QTextEdit(DEFAULT_PROMPT)
        s_layout.addWidget(QLabel("Промпт"))
        s_layout.addWidget(self.prompt_edit)

        btn_save = QPushButton("💾 СОХРАНИТЬ")
        btn_save.clicked.connect(self.save_settings)
        s_layout.addWidget(btn_save)
        settings.setLayout(s_layout)
        tabs.addTab(settings, "⚙️")

    # ---------------- ЛОГИКА UI ----------------
    def _add_log(self, t):
        self.log_widget.append(t)

    def _set_status(self, t):
        self.status_label.setText(t)

    def _set_btn_auto_text(self, t):
        self.btn_auto.setText(t)

    def _set_text(self, t):
        self.output.setHtml(t.replace("\n", "<br>")); pyperclip.copy(t)

    def save_settings(self):
        data = {"token": self.token_input.text(), "prompt": self.prompt_edit.toPlainText(),
                "whisper": self.whisper_input.text(), "auto_interval": self.auto_interval_input.text()}
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.signals.log.emit("✅ Настройки сохранены")

    def load_settings(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                self.token_input.setText(d.get("token", ""))
                self.prompt_edit.setText(d.get("prompt", DEFAULT_PROMPT))
                self.whisper_input.setText(d.get("whisper", "base"))
                self.auto_interval_input.setText(d.get("auto_interval", "15"))

    def filter_text(self, text):
        """Очистка текста от мусора Whisper"""
        text = re.sub(r'(\w+)(?:-\1)+', r'\1', text, flags=re.IGNORECASE)
        if any(g in text.lower() for g in ["субтитры", "редактор", "музыка"]) or len(text.strip()) < 2:
            return ""
        return text.strip()

    # ---------------- РЕЖИМЫ ЗАПИСИ ----------------

    def toggle_mic_mode(self):
        """Режим 'МИК': Нажали - пишет ваш голос, отжали - отправил в AI."""
        if self.btn_mic.isChecked():
            if self.auto_mode: self.toggle_auto_mode()  # Выключаем авто, если включено
            self.mic_mode = True
            self.is_running = True
            self.accumulated_text = ""
            self.signals.status.emit("🔴 ЗАПИСЬ МИКРОФОНА")
            threading.Thread(target=self.audio_engine, args=(True,), daemon=True).start()
        else:
            self.is_running = False
            self.signals.status.emit("⌛ ОБРАБОТКА...")
            # При выключении МИК, AI сразу получит всё что вы сказали
            QTimer.singleShot(500, self.trigger_ai_send)

    def toggle_auto_mode(self):
        """Режим 'АВТО': Постоянно слушает систему и шлет куски по таймеру."""
        if self.btn_auto.isChecked():
            if self.mic_mode: self.btn_mic.setChecked(False); self.mic_mode = False

            try:
                interval = int(self.auto_interval_input.text())
            except:
                interval = 15

            self.auto_mode = True
            self.is_running = True
            self.accumulated_text = ""
            self.auto_seconds_left = interval

            self.signals.status.emit("▶️ АВТО-СЛУШАНИЕ")
            threading.Thread(target=self.audio_engine, args=(False,), daemon=True).start()

            self.auto_timer.start(interval * 1000)
            self.countdown_timer.start(1000)
        else:
            self.stop_all_audio()

    def stop_all_audio(self):
        self.is_running = False
        self.auto_mode = False
        self.auto_timer.stop()
        self.countdown_timer.stop()
        self.btn_auto.setText("🤖 АВТО")
        self.signals.status.emit("⚪ ГОТОВ")

    def update_countdown(self):
        self.auto_seconds_left -= 1
        if self.auto_seconds_left < 0:
            try:
                self.auto_seconds_left = int(self.auto_interval_input.text()) - 1
            except:
                self.auto_seconds_left = 14
        self.signals.btn_auto_text.emit(f"🤖 АВТО ({self.auto_seconds_left}s)")

    # ---------------- AUDIO ENGINE ----------------

    def audio_engine(self, use_mic=False):
        """
        Единый движок записи.
        use_mic=True  -> пишет с микрофона (Input Device)
        use_mic=False -> пишет с динамиков (Loopback Device)
        """
        try:
            if not self.whisper_model:
                self.signals.log.emit("⏳ Загрузка Whisper...")
                self.whisper_model = WhisperModel(self.whisper_input.text(), device="cpu", compute_type="int8")

            p = pyaudio.PyAudio()

            if use_mic:
                # Берем стандартный вход (Микрофон)
                device_info = p.get_default_input_device_info()
            else:
                # Ищем WASAPI Loopback (Звук системы)
                wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
                device_info = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
                if not device_info["isLoopbackDevice"]:
                    for loopback in p.get_loopback_device_info_generator():
                        if device_info["name"] in loopback["name"]:
                            device_info = loopback
                            break

            samplerate = int(device_info["defaultSampleRate"])
            channels = device_info["maxInputChannels"]

            stream = p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=samplerate,
                input=True,
                input_device_index=device_info["index"],
                frames_per_buffer=1024
            )

            audio_buffer = []
            # Обработка каждые 3 секунды для отзывчивости
            analyze_frames = int(samplerate / 1024 * 3)

            while self.is_running:
                data = stream.read(1024, exception_on_overflow=False)
                audio_buffer.append(data)

                if len(audio_buffer) >= analyze_frames:
                    raw_audio = b"".join(audio_buffer)
                    audio_np = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32)
                    if channels > 1: audio_np = audio_np.reshape(-1, channels).mean(axis=1)
                    audio_np /= 32768.0  # Нормализация

                    # Ресемплинг до 16кГц
                    if samplerate != 16000:
                        audio_np = np.interp(
                            np.linspace(0, len(audio_np), int(len(audio_np) * 16000 / samplerate)),
                            np.arange(len(audio_np)), audio_np
                        )

                    # Транскрибация
                    if np.max(np.abs(audio_np)) > 0.02:
                        segments, _ = self.whisper_model.transcribe(audio_np, language="ru")
                        for s in segments:
                            txt = self.filter_text(s.text)
                            if txt:
                                self.accumulated_text += " " + txt
                                self.signals.log.emit(f"🎤 {txt}")

                    audio_buffer = []

            stream.stop_stream()
            stream.close()
            p.terminate()

        except Exception as e:
            self.signals.log.emit(f"🚨 Audio Error: {e}")

    # ---------------- AI & UTILS ----------------

    def trigger_ai_send(self):
        """Отправка накопленного текста в нейросеть"""
        text = self.accumulated_text.strip()
        if text:
            self.signals.log.emit("📤 Отправка в AI...")
            threading.Thread(target=self.ask_ai, args=(text,), daemon=True).start()
            self.accumulated_text = ""
        elif not self.is_running and self.mic_mode:
            # Если микрофон выключили, а текста нет
            self.signals.status.emit("⚪ ГОТОВ")

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
                    "messages": [
                        {"role": "system", "content": self.prompt_edit.toPlainText()},
                        {"role": "user", "content": content}
                    ]
                }, timeout=40
            )
            if r.status_code == 200:
                ans = r.json()["choices"][0]["message"]["content"]
                self.signals.text.emit(ans)
        except Exception as e:
            self.signals.log.emit(f"🌐 AI Error: {e}")

    def send_manual_text(self):
        t = self.input.text()
        if t:
            self.input.clear()
            threading.Thread(target=self.ask_ai, args=(t,), daemon=True).start()

    def take_screenshot(self):
        try:
            self.signals.log.emit("📸 Анализ экрана...")
            img = ImageGrab.grab()
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=70)
            img_str = base64.b64encode(buf.getvalue()).decode('utf-8')
            threading.Thread(target=self.ask_ai, args=("Реши задачу с экрана", img_str), daemon=True).start()
        except Exception as e:
            self.signals.log.emit(f"📸 Screen Error: {e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = InterviewAssistantWin()
    win.show()
    sys.exit(app.exec())
