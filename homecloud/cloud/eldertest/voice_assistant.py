# voice_assistant.py
import google.generativeai as genai
import logging
import os

logger = logging.getLogger(__name__)


class VoiceAssistant:
    def __init__(self, gemini_api_key):
        """
        語音 AI 助手
        gemini_api_key: Gemini API 金鑰
        """
        self.api_key = gemini_api_key
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel("gemini-2.5-flash")
        self.chat_history = []

        # 系統提示
        self.system_prompt = """你是一個長者陪伴 AI 助手，名為「汪汪隊助手」。
你的職責是：
1. 關心長輩的健康狀況
2. 提供溫暖的陪伴和對話
3. 回答簡單的問題
4. 在緊急情況下提供建議

請使用親切、溫暖的語氣，像家人一樣關心長輩。
回答要簡潔明瞭，避免使用複雜的專業術語。"""

        logger.info("✅ 語音 AI 助手已初始化")

    def process_voice_input(self, text):
        """
        處理語音輸入的文字
        輸入：語音識別後的文字
        輸出：AI 回應的文字
        """
        try:
            # 添加系統提示（如果是第一條訊息）
            if not self.chat_history:
                full_message = f"{self.system_prompt}\n\n用戶：{text}"
            else:
                full_message = text

            # 發送給 Gemini
            response = self.model.generate_content(full_message)

            # 記錄對話歷史
            self.chat_history.append({
                'role': 'user',
                'content': text
            })
            self.chat_history.append({
                'role': 'assistant',
                'content': response.text
            })

            # 限制歷史長度
            if len(self.chat_history) > 20:
                self.chat_history = self.chat_history[-20:]

            logger.info(f"🎤 語音輸入：{text[:50]}...")
            logger.info(f"🤖 AI 回應：{response.text[:50]}...")

            return response.text

        except Exception as e:
            logger.error(f"語音處理失敗：{e}")
            return "抱歉，我現在有點不舒服，請稍後再試試看。🐶"

    def clear_history(self):
        """清除對話歷史"""
        self.chat_history = []
        logger.info("對話歷史已清除")

    def get_health_advice(self, symptom):
        """獲取健康建議"""
        prompt = f"""長輩表示有以下症狀：{symptom}
請提供簡單的健康建議，包括：
1. 是否需要立即就醫
2. 居家護理建議
3. 注意事項

請用溫暖關心的語氣回答。"""

        return self.process_voice_input(prompt)

# 全域實例（需要 API Key）
# voice_assistant = VoiceAssistant(gemini_api_key="YOUR_API_KEY")