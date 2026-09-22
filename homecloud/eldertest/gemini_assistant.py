import google.generativeai as genai
import logging
import config
from datetime import datetime

logger = logging.getLogger(__name__)


class GeminiAssistant:
    def __init__(self):
        # 配置 API
        genai.configure(api_key=config.GEMINI_API_KEY)

        # 初始化模型
        self.model = genai.GenerativeModel('gemini-2.5-flash')

        # 對話歷史
        self.chat_history = []

        # 系統提示
        self.system_prompt = """你是一個長者陪伴 AI 助手，名為「汪汪隊助手」。
你的職責是：
1. 關心長輩的健康狀況
2. 提醒按時吃藥和休息
3. 提供溫暖的陪伴和對話
4. 在緊急情況下提供建議

請使用親切、溫暖的語氣，像家人一樣關心長輩。
回答要簡潔明瞭，避免使用複雜的專業術語。"""

        # 初始化對話
        self.chat = self.model.start_chat(history=[])

    def send_message(self, user_message):
        """發送訊息並獲取回應"""
        try:
            # 添加系統提示（如果是第一條訊息）
            if not self.chat_history:
                full_message = f"{self.system_prompt}\n\n用戶：{user_message}"
            else:
                full_message = user_message

            # 發送訊息
            response = self.chat.send_message(full_message)

            # 記錄歷史
            self.chat_history.append({
                'role': 'user',
                'content': user_message,
                'time': datetime.now().strftime('%H:%M:%S')
            })
            self.chat_history.append({
                'role': 'assistant',
                'content': response.text,
                'time': datetime.now().strftime('%H:%M:%S')
            })

            logger.info(f"Gemini 回應：{response.text[:100]}...")

            return response.text

        except Exception as e:
            logger.error(f"Gemini API 錯誤：{e}")
            return "抱歉，我現在有點不舒服，請稍後再試試看。🐶"

    def get_health_advice(self, symptom):
        """獲取健康建議"""
        prompt = f"""長輩表示有以下症狀：{symptom}
請提供簡單的健康建議，包括：
1. 是否需要立即就醫
2. 居家護理建議
3. 注意事項

請用溫暖關心的語氣回答。"""

        return self.send_message(prompt)

    def get_medication_info(self, medicine_name):
        """獲取藥品資訊"""
        prompt = f"""請說明藥品「{medicine_name}」的：
1. 主要功效
2. 常見副作用
3. 服用注意事項
4. 與其他藥物的交互作用

請用簡單易懂的方式說明。"""

        return self.send_message(prompt)

    def get_daily_greeting(self):
        """獲取每日問候"""
        hour = datetime.now().hour

        if hour < 6:
            greeting = "深夜問候"
        elif hour < 12:
            greeting = "早安問候"
        elif hour < 18:
            greeting = "午安問候"
        else:
            greeting = "晚安問候"

        prompt = f"""請給長輩一個溫暖的{greeting}，包括：
1. 問候語
2. 天氣提醒（假設）
3. 健康小提醒
4. 鼓勵的話

請保持簡短溫馨。"""

        return self.send_message(prompt)

    def clear_history(self):
        """清除對話歷史"""
        self.chat_history = []
        self.chat = self.model.start_chat(history=[])
        logger.info("對話歷史已清除")

    def get_chat_history(self, limit=10):
        """獲取對話歷史"""
        return self.chat_history[-limit:]


# 全域實例
gemini_assistant = GeminiAssistant()