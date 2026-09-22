import requests
import logging
from datetime import datetime
import config

logger = logging.getLogger(__name__)


class LineNotifier:
    def __init__(self):
        self.access_token = config.LINE_CHANNEL_ACCESS_TOKEN
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.access_token}'
        }
        self.api_url = "https://api.line.me/v2/bot/message/push"
        self.user_id = None  # 需要從 LINE 管理後台獲取

    def send_message(self, message, user_id=None):
        """發送 LINE 文字訊息"""
        try:
            target_id = user_id or self.user_id
            if not target_id:
                logger.warning("未設定 LINE User ID，使用 broadcast")
                return self.broadcast_message(message)

            payload = {
                "to": target_id,
                "messages": [
                    {
                        "type": "text",
                        "text": message
                    }
                ]
            }

            response = requests.post(
                self.api_url,
                headers=self.headers,
                json=payload,
                timeout=10
            )

            if response.status_code == 200:
                logger.info(f"LINE 訊息發送成功：{message[:50]}...")
                return True
            else:
                logger.error(f"LINE 訊息發送失敗：{response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"LINE 訊息發送錯誤：{e}")
            return False

    def broadcast_message(self, message):
        """廣播訊息給所有用戶"""
        try:
            broadcast_url = "https://api.line.me/v2/bot/message/broadcast"
            payload = {
                "messages": [
                    {
                        "type": "text",
                        "text": message
                    }
                ]
            }

            response = requests.post(
                broadcast_url,
                headers=self.headers,
                json=payload,
                timeout=10
            )

            if response.status_code == 200:
                logger.info(f"LINE 廣播成功：{message[:50]}...")
                return True
            else:
                logger.error(f"LINE 廣播失敗：{response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"LINE 廣播錯誤：{e}")
            return False

    def send_fall_alert(self):
        """發送跌倒警告"""
        message = f"""⚠️【緊急警告】跌倒檢測！

📍 時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🚨 狀態：偵測到可能跌倒
⏱️ 持續時間：超過 3 秒

請立即確認長輩安全狀況！"""
        return self.send_message(message)

    def send_immobile_alert(self, duration):
        """發送長時間不動警告"""
        message = f"""⚠️【健康警告】長時間未活動

📍 時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⏱️ 未活動時間：{duration:.0f} 秒
💡 建議：請確認長輩是否需要協助

請主動關心長輩狀況！"""
        return self.send_message(message)

    def send_medication_reminder(self, medicine_name="藥物"):
        """發送吃藥提醒"""
        message = f"""💊【吃藥提醒】

📍 時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
💊 藥品：{medicine_name}
⏰ 請按時服用藥物

保持健康，按時吃藥！💪"""
        return self.send_message(message)

    def send_follow_status(self, status):
        """發送跟隨狀態更新"""
        message = f"""🤖【機器人狀態】

📍 時間：{datetime.now().strftime('%H:%M:%S')}
👤 跟隨狀態：{status}
🎯 模式：主人跟隨模式

機器人正常運作中！✅"""
        return self.send_message(message)

    def test_connection(self):
        """測試 LINE 連線"""
        message = "🎉 LINE 通知系統測試成功！\n\n系統正常運作中。"
        return self.send_message(message)


# 全域實例
line_notifier = LineNotifier()