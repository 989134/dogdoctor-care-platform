import time
import logging
from datetime import datetime
import threading
import config
from line_notifier import line_notifier

logger = logging.getLogger(__name__)


class MedicationReminder:
    def __init__(self):
        # 提醒時間
        self.medication_times = config.MEDICATION_TIMES
        self.reminder_interval = config.MEDICATION_REMINDER_INTERVAL

        # 狀態
        self.enabled = config.HEALTH_REMINDER_ENABLED
        self.last_reminder_time = {}
        self.active = False

        # 線程
        self.check_thread = None
        self.stop_event = threading.Event()

        # 藥品清單
        self.medicine_list = {
            "08:00": "早晨藥物",
            "12:00": "中午藥物",
            "20:00": "晚上藥物"
        }

    def start(self):
        """啟動提醒服務"""
        if not self.enabled:
            logger.info("健康提醒已禁用")
            return

        self.active = True
        self.stop_event.clear()
        self.check_thread = threading.Thread(target=self._check_loop, daemon=True)
        self.check_thread.start()
        logger.info("吃藥提醒服務已啟動")

    def stop(self):
        """停止提醒服務"""
        self.active = False
        self.stop_event.set()
        if self.check_thread:
            self.check_thread.join(timeout=2)
        logger.info("吃藥提醒服務已停止")

    def _check_loop(self):
        """檢查循環"""
        while self.active and not self.stop_event.is_set():
            try:
                self._check_reminders()
                time.sleep(60)  # 每分鐘檢查一次
            except Exception as e:
                logger.error(f"提醒檢查錯誤：{e}")
                time.sleep(60)

    def _check_reminders(self):
        """檢查是否需要提醒"""
        current_time = datetime.now().strftime("%H:%M")
        current_date = datetime.now().strftime("%Y-%m-%d")

        for med_time in self.medication_times:
            if current_time == med_time:
                # 檢查是否已提醒過（今天）
                reminder_key = f"{current_date}_{med_time}"

                if reminder_key not in self.last_reminder_time:
                    # 發送提醒
                    medicine_name = self.medicine_list.get(med_time, "藥物")
                    self._send_reminder(medicine_name, med_time)
                    self.last_reminder_time[reminder_key] = datetime.now()

    def _send_reminder(self, medicine_name, time_str):
        """發送提醒"""
        logger.info(f"發送吃藥提醒：{medicine_name} - {time_str}")

        # LINE 通知
        line_notifier.send_medication_reminder(medicine_name)

        # 日誌記錄
        logger.info(f"吃藥提醒已發送：{medicine_name}")

    def add_reminder_time(self, time_str, medicine_name="藥物"):
        """添加提醒時間"""
        if time_str not in self.medication_times:
            self.medication_times.append(time_str)
            self.medicine_list[time_str] = medicine_name
            logger.info(f"添加提醒時間：{time_str} - {medicine_name}")

    def remove_reminder_time(self, time_str):
        """移除提醒時間"""
        if time_str in self.medication_times:
            self.medication_times.remove(time_str)
            self.medicine_list.pop(time_str, None)
            logger.info(f"移除提醒時間：{time_str}")

    def get_schedule(self):
        """獲取提醒日程"""
        schedule = []
        for time_str in sorted(self.medication_times):
            medicine = self.medicine_list.get(time_str, "藥物")
            schedule.append({
                'time': time_str,
                'medicine': medicine
            })
        return schedule

    def manual_remind(self, medicine_name="藥物"):
        """手動發送提醒"""
        self._send_reminder(medicine_name, datetime.now().strftime("%H:%M"))


# 全域實例
medication_reminder = MedicationReminder()