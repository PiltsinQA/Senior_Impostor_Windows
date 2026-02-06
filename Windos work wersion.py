import sys
import threading
import json
import os
import requests
import base64
import ctypes
import re
import numpy as np
import pyaudiowpatch as pyaudio
import pyperclip
from io import BytesIO
from PIL import ImageGrab
from faster_whisper import WhisperModel

from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow, QTabWidget,
                             QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
                             QLineEdit, QPushButton, QProgressBar)
from PyQt6.QtCore import QTimer, pyqtSignal, QObject, Qt
from PyQt6.QtGui import QKeyEvent

# Константы для Stealth-режима
WDA_EXCLUDEFROMCAPTURE = 0x00000011
CONFIG_FILE = "settings_win.json"
DEFAULT_PROMPT = "Ты — Senior QA. Отвечай кратко и четко."


# ================= SAFE QT SIGNALS =================
class SafeSignals(QObject):
    log = pyqtSignal(str)
    text = pyqtSignal(str)
    status = pyqtSignal(str)
    btn_auto_text = pyqtSignal(str)
    volume = pyqtSignal(int)  # Сигнал для передачи уровня громкости (0-100)


# ================= MAIN WINDOW =================
class InterviewAssistantWin(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stealth Assistant PRO v5.2")
        self.resize(460, 800)

        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # Окно может получать фокус

        self.signals = SafeSignals()
        self.signals.log.connect(self._add_log)
        self.signals.text.connect(self._add_to_history)
        self.signals.status.connect(self._set_status)
        self.signals.btn_auto_text.connect(self._set_btn_auto_text)
        self.signals.volume.connect(self._update_volume)

        # Состояния
        self.is_running = False
        self.auto_mode = False
        self.mic_mode = False
        self.whisper_model = None
        self.accumulated_text = ""

        # История сообщений
        self.history = []
        self.history_index = -1

        self.auto_timer = QTimer()
        self.auto_timer.timeout.connect(self.trigger_ai_send)
        self.countdown_timer = QTimer()
        self.countdown_timer.timeout.connect(self.update_countdown)
        self.auto_seconds_left = 0

        self.init_ui()
        self.load_settings()
        self.update_button_styles()  # Инициализация стилей кнопок

        QTimer.singleShot(500, self.apply_hard_stealth)

    def apply_hard_stealth(self):
        try:
            hwnd = int(self.winId())
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

        # Поле вывода ответа AI
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Здесь появятся ответы AI. Используйте стрелки ← → для навигации.")
        layout.addWidget(self.output)

        # Индикатор истории (номер сообщения)
        self.history_label = QLabel("История: 0/0")
        self.history_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.history_label)

        # Визуальная шкала громкости
        vol_layout = QHBoxLayout()
        vol_layout.addWidget(QLabel("🎤"))
        self.volume_bar = QProgressBar()
        self.volume_bar.setRange(0, 100)
        self.volume_bar.setTextVisible(False)
        self.volume_bar.setFixedHeight(10)
        self.volume_bar.setStyleSheet("""
            QProgressBar { border: 1px solid grey; border-radius: 5px; background: #222; }
            QProgressBar::chunk { background-color: #00ff00; width: 2px; }
        """)
        vol_layout.addWidget(self.volume_bar)
        layout.addLayout(vol_layout)

        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        self.log_widget.setFixedHeight(80)
        self.log_widget.setStyleSheet(
            "background: #1e1e1e; color: #00ff00; font-family: 'Courier New'; font-size: 10px;")
        layout.addWidget(self.log_widget)

        # Навигация по истории (Кнопки V)
        nav_row = QHBoxLayout()
        self.btn_prev = QPushButton("◀ Пред (←)")
        self.btn_prev.clicked.connect(self.prev_message)
        self.btn_prev.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # Не получает фокус от стрелок
        self.btn_next = QPushButton("След (→) ▶")
        self.btn_next.clicked.connect(self.next_message)
        self.btn_next.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # Не получает фокус от стрелок
        nav_row.addWidget(self.btn_prev)
        nav_row.addWidget(self.btn_next)
        layout.addLayout(nav_row)

        # Управление
        row = QHBoxLayout()
        self.btn_mic = QPushButton("🎙 МИК")
        self.btn_mic.setCheckable(True)
        self.btn_mic.setFixedHeight(40)
        self.btn_mic.clicked.connect(self.toggle_mic_mode)
        self.btn_mic.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # Не получает фокус от стрелок
        row.addWidget(self.btn_mic)

        self.btn_auto = QPushButton("🤖 АВТО")
        self.btn_auto.setCheckable(True)
        self.btn_auto.setFixedHeight(40)
        self.btn_auto.clicked.connect(self.toggle_auto_mode)
        self.btn_auto.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # Не получает фокус от стрелок
        row.addWidget(self.btn_auto)

        btn_scr = QPushButton("📸 SCR")
        btn_scr.setFixedHeight(40)
        btn_scr.clicked.connect(self.take_screenshot)
        btn_scr.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # Не получает фокус от стрелок
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
        btn_save.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        s_layout.addWidget(btn_save)
        settings.setLayout(s_layout)
        tabs.addTab(settings, "⚙️")

    # ---------------- СТИЛИ КНОПОК ----------------
    def update_button_styles(self):
        """Обновляет цвета кнопок в зависимости от состояния"""
        # Стиль для МИК
        if self.mic_mode and self.is_running:
            self.btn_mic.setStyleSheet("""
                QPushButton {
                    background-color: #e74c3c;
                    color: white;
                    font-weight: bold;
                    border: 2px solid #c0392b;
                }
                QPushButton:hover {
                    background-color: #c0392b;
                }
            """)
        else:
            self.btn_mic.setStyleSheet("""
                QPushButton {
                    background-color: #2980b9;
                    color: white;
                    border: 1px solid #3498db;
                }
                QPushButton:hover {
                    background-color: #3498db;
                }
            """)

        # Стиль для АВТО
        if self.auto_mode and self.is_running:
            self.btn_auto.setStyleSheet("""
                QPushButton {
                    background-color: #27ae60;
                    color: white;
                    font-weight: bold;
                    border: 2px solid #219653;
                }
                QPushButton:hover {
                    background-color: #219653;
                }
            """)
        else:
            self.btn_auto.setStyleSheet("""
                QPushButton {
                    background-color: #7f8c8d;
                    color: white;
                    border: 1px solid #95a5a6;
                }
                QPushButton:hover {
                    background-color: #95a5a6;
                }
            """)

    # ---------------- ЛОГИКА ИСТОРИИ ----------------
    def _add_to_history(self, text):
        """Добавляет новый ответ в историю и отображает его"""
        self.history.append(text)
        self.history_index = len(self.history) - 1
        self._display_current_message()

    def _display_current_message(self):
        """Отображает сообщение из истории по текущему индексу"""
        if 0 <= self.history_index < len(self.history):
            msg = self.history[self.history_index]
            self.output.setHtml(msg.replace("\n", "<br>"))
            pyperclip.copy(msg)
            self.history_label.setText(f"История: {self.history_index + 1}/{len(self.history)}")
        else:
            self.output.clear()
            self.history_label.setText("История: 0/0")

    def prev_message(self):
        if self.history_index > 0:
            self.history_index -= 1
            self._display_current_message()

    def next_message(self):
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self._display_current_message()

    def keyPressEvent(self, event):
        """Глобальная обработка стрелок ←/→ как горячих клавиш для навигации по истории"""
        # Стрелки ←/→ работают как горячие клавиши ВСЕГДА, кроме поля ввода текста
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            focused_widget = QApplication.focusWidget()

            # Если фокус в поле ввода текста — оставляем стандартное поведение (редактирование)
            if isinstance(focused_widget, QLineEdit):
                super().keyPressEvent(event)
                return

            # Для всех остальных случаев — навигация по истории
            if event.key() == Qt.Key.Key_Left:
                self.prev_message()
                event.accept()
                return
            elif event.key() == Qt.Key.Key_Right:
                self.next_message()
                event.accept()
                return

        # Стандартная обработка остальных клавиш
        super().keyPressEvent(event)

    # ---------------- ГРОМКОСТЬ ----------------
    def _update_volume(self, val):
        self.volume_bar.setValue(val)

    # ---------------- ОСТАЛЬНАЯ ЛОГИКА ----------------
    def _add_log(self, t):
        self.log_widget.append(t)

    def _set_status(self, t):
        self.status_label.setText(t)

    def _set_btn_auto_text(self, t):
        self.btn_auto.setText(t)

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
        text = re.sub(r'(\w+)(?:-\1)+', r'\1', text, flags=re.IGNORECASE)
        if any(g in text.lower() for g in ["субтитры", "редактор", "музыка"]) or len(text.strip()) < 2:
            return ""
        return text.strip()

    def toggle_mic_mode(self):
        if self.btn_mic.isChecked():
            if self.auto_mode:
                self.toggle_auto_mode()
            self.mic_mode = True
            self.is_running = True
            self.accumulated_text = ""
            self.signals.status.emit("🔴 ЗАПИСЬ МИКРОФОНА")
            self.update_button_styles()  # Обновляем стиль кнопки
            threading.Thread(target=self.audio_engine, args=(True,), daemon=True).start()
        else:
            self.is_running = False
            self.mic_mode = False
            self.signals.status.emit("⌛ ОБРАБОТКА...")
            self.update_button_styles()  # Обновляем стиль кнопки
            self.signals.volume.emit(0)
            QTimer.singleShot(500, self.trigger_ai_send)

    def toggle_auto_mode(self):
        if self.btn_auto.isChecked():
            if self.mic_mode:
                self.btn_mic.setChecked(False)
                self.toggle_mic_mode()
            try:
                interval = int(self.auto_interval_input.text())
            except:
                interval = 15
            self.auto_mode = True
            self.is_running = True
            self.accumulated_text = ""
            self.auto_seconds_left = interval
            self.signals.status.emit("▶️ АВТО-СЛУШАНИЕ")
            self.update_button_styles()  # Обновляем стиль кнопки
            threading.Thread(target=self.audio_engine, args=(False,), daemon=True).start()
            self.auto_timer.start(interval * 1000)
            self.countdown_timer.start(1000)
            self.update_countdown()  # Сразу обновляем текст кнопки
        else:
            self.stop_all_audio()
            self.update_button_styles()  # Обновляем стиль кнопки

    def stop_all_audio(self):
        self.is_running = False
        self.auto_mode = False
        self.auto_timer.stop()
        self.countdown_timer.stop()
        self.btn_auto.setText("🤖 АВТО")
        self.signals.status.emit("⚪ ГОТОВ")
        self.signals.volume.emit(0)

    def update_countdown(self):
        self.auto_seconds_left -= 1
        if self.auto_seconds_left < 0:
            try:
                self.auto_seconds_left = int(self.auto_interval_input.text()) - 1
            except:
                self.auto_seconds_left = 14
        self.signals.btn_auto_text.emit(f"🤖 АВТО ({self.auto_seconds_left}s)")

    def audio_engine(self, use_mic=False):
        try:
            if not self.whisper_model:
                self.signals.log.emit("⏳ Загрузка Whisper...")
                self.whisper_model = WhisperModel(self.whisper_input.text(), device="cpu", compute_type="int8")

            p = pyaudio.PyAudio()
            if use_mic:
                device_info = p.get_default_input_device_info()
            else:
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
            analyze_frames = int(samplerate / 1024 * 3)

            while self.is_running:
                data = stream.read(1024, exception_on_overflow=False)

                # Расчет громкости (RMS/Пик)
                audio_data = np.frombuffer(data, dtype=np.int16)
                peak = np.abs(audio_data).max()
                normalized_vol = min(100, int((peak / 20000) * 100))
                self.signals.volume.emit(normalized_vol)

                audio_buffer.append(data)

                if len(audio_buffer) >= analyze_frames:
                    raw_audio = b"".join(audio_buffer)
                    audio_np = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32)
                    if channels > 1: audio_np = audio_np.reshape(-1, channels).mean(axis=1)
                    audio_np /= 32768.0

                    if samplerate != 16000:
                        audio_np = np.interp(
                            np.linspace(0, len(audio_np), int(len(audio_np) * 16000 / samplerate)),
                            np.arange(len(audio_np)), audio_np
                        )

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

    def trigger_ai_send(self):
        text = self.accumulated_text.strip()
        if text:
            self.signals.log.emit("📤 Отправка в AI...")
            threading.Thread(target=self.ask_ai, args=(text,), daemon=True).start()
            self.accumulated_text = ""
        elif not self.is_running and self.mic_mode:
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