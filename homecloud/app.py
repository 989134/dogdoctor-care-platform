# app.py
import os
from flask import Flask, request, abort, render_template, jsonify, Response
from flask_cors import CORS
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai
from dotenv import load_dotenv
import threading

# === 匯入機器人邏輯 ===
import s  # 匯入我們剛才修改的 s.py

load_dotenv()

app = Flask(__name__)
CORS(app)

# --- 機器人全域變數引用 ---
# 直接引用 s.py 中的全域變數，實現記憶體共享
global_frame_lock = s.global_frame_lock
global_jpeg_output = None  # 本地引用，實際指向 s.global_jpeg_output

# --- 啟動機器人系統 ---
# 在 Flask 啟動前，先啟動機器人執行緒
robot_stop_event = None
try:
    robot_stop_event = s.start_robot_system()
    print("✅ 機器人系統已啟動")
except Exception as e:
    print(f"⚠️ 機器人系統啟動失敗 (可忽略若僅測試網頁): {e}")

# LINE API
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# Gemini
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel("models/gemini-2.5-flash")


# 首頁 (更新 HTML 以包含鏡頭)
@app.route("/")
def home():
    # 這裡直接使用您提供的 HTML 內容，並嵌入了視訊標籤
    html_content = """
    <!DOCTYPE html>
    <html lang="zh-Hant">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>汪汪隊立大功｜長者陪伴系統</title>
    <style>
        :root{--bg1:#0b1220;--bg2:#111b33;--card:#121c36cc;--text:#e8eefc;--line:#22c55e;--border:rgba(255,255,255,.1);--radius:20px;}
        body{margin:0;font-family:Arial;color:var(--text);background:linear-gradient(180deg,var(--bg1),var(--bg2));}
        .wrap{width:min(1100px,92vw);margin:auto;padding:25px;}
        .grid{display:grid;grid-template-columns:1.2fr .8fr;gap:15px;margin-top:20px;}
        @media(max-width:880px){.grid{grid-template-columns:1fr;}}
        .card{border:1px solid var(--border);border-radius:var(--radius);background:rgba(255,255,255,.05);}
        .cardHead{padding:15px;border-bottom:1px solid rgba(255,255,255,.1);}
        .video-container { width: 100%; height: 400px; background: #000; display: flex; align-items: center; justify-content: center; overflow: hidden; }
        .video-container img { width: 100%; height: 100%; object-fit: cover; }
        .chatBody{display:flex;flex-direction:column;height:300px;padding:10px;}
        .chatLog{flex:1;overflow:auto;padding:10px;background:rgba(0,0,0,.2);border-radius:10px;}
        .msg{display:flex;gap:10px;margin:10px 0;}
        .bubble{padding:8px 10px;background:rgba(255,255,255,.05);border-radius:10px;max-width:70%;font-size:14px;}
        .bubble.you{background:rgba(59,130,246,.2);}
        .controls{display:flex;gap:10px;margin-top:10px;}
        .input{flex:1;border:1px solid var(--border);border-radius:10px;padding:5px;}
        input{width:100%;background:none;border:none;color:white;outline:none;}
        .btn{border:none;padding:8px 12px;border-radius:10px;cursor:pointer;background:#334155;color:white;}
        .btnPrimary{background:#3b82f6;}
        .pill{padding:6px 12px;border-radius:30px;border:1px solid var(--border);background:rgba(255,255,255,.04);font-size:13px;}
        .topbar{display:flex;justify-content:space-between;align-items:center;background:rgba(255,255,255,.05);border:1px solid var(--border);border-radius:var(--radius);padding:15px;}
    </style>
    </head>
    <body>
    <div class="wrap">
        <div class="topbar">
            <div class="brand"><div class="logo">🐶</div><div><h2>汪汪隊立大功</h2><p style="font-size:12px;color:#9ca3af">長者陪伴 AI 系統</p></div></div>
            <div class="statusRow"><div class="pill">系統運行中</div><div class="pill" id="lineStatusTop">LINE 未連線</div></div>
        </div>
        <div class="grid">
            <!-- 鏡頭畫面 (新增) -->
            <div class="card">
                <div class="cardHead"><h3 class="cardTitle">📹 即時鏡頭 (主人追蹤)</h3></div>
                <div class="video-container">
                    <img src="{{ url_for('video_feed') }}" alt="Camera Stream">
                </div>
            </div>
            <!-- 聊天與控制 -->
            <div class="stack">
                <div class="card">
                    <div class="cardHead"><h3 class="cardTitle">🎤 AI 聊天</h3></div>
                    <div class="chatBody">
                        <div id="chatLog" class="chatLog"><div class="msg"><div class="bubble">你好，我是汪汪隊助手 😊</div></div></div>
                        <div class="controls">
                            <div class="input"><input id="userText" placeholder="輸入訊息"></div>
                            <button class="btn btnPrimary" onclick="sendText()">送出</button>
                        </div>
                    </div>
                </div>
                <div class="card">
                    <div class="cardHead"><h3 class="cardTitle">📱 LINE 同步</h3></div>
                    <div style="padding:15px;"><button class="btn" onclick="testLine()">測試 LINE</button></div>
                </div>
            </div>
        </div>
    </div>
    <script>
        function sendText(){
            const input=document.getElementById("userText");
            const text=input.value;
            if(!text)return;
            const chat=document.getElementById("chatLog");
            chat.innerHTML += `<div class="msg"><div class="bubble you">${text}</div></div>`;
            input.value="";
            fetch("/ai", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({text:text})})
            .then(r=>r.json()).then(d=>{
                chat.innerHTML += `<div class="msg"><div class="bubble">${d.reply}</div></div>`;
                chat.scrollTop=chat.scrollHeight;
            });
        }
        function testLine(){
            fetch("/send_line").then(r=>r.json()).then(d=>alert(d.status));
        }
    </script>
    </body>
    </html>
    """
    return render_template_string(html_content)


# --- 視訊串流路由 (核心功能) ---
@app.route('/video_feed')
def video_feed():
    def generate():
        while True:
            jpeg_frame = None
            # 從 s.py 的全域變數獲取最新畫面
            with global_frame_lock:
                if s.global_jpeg_output is not None:
                    jpeg_frame = s.global_jpeg_output

            if jpeg_frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpeg_frame + b'\r\n')
            else:
                # 若無畫面，傳送一張黑色圖片避免斷線
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7telecast(44444444444444444444444444444444444444444444444444\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd5\xff\xd9')
            import time
            time.sleep(1 / 15)  # 限制 FPS

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')


# --- 機器人控制路由 (接收 s.py 的指令) ---
@app.route("/control", methods=["POST"])
def control_robot():
    # 這裡可以實作實際發送串口指令給 Arduino 的邏輯
    # 目前僅記錄日誌，實際硬體請在此處加入 serial.write()
    data = request.json
    command = data.get("command")
    print(f"🤖 執行機器人指令：{command}")
    # 範例：serial_port.write(command.encode())
    result = s.send_robot_command(command)
    status_code = 200 if result.get("ok") else 400
    return jsonify({"status": "ok" if result.get("ok") else "error", **result}), status_code


# --- 原有 LINE 與 AI 路由 (保持不變) ---
@app.route("/health/water")
def health_water():
    return jsonify({"status": "ok", "msg": "喝水提醒成功"})


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_input = event.message.text
    response = model.generate_content(user_input)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=response.text))


@app.route("/send_line")
def send_line():
    try:
        line_bot_api.broadcast(TextSendMessage(text="LINE 測試成功 🎉"))
        return jsonify({"status": "success", "ok": True})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/ai", methods=["POST"])
def ai_chat():
    try:
        data = request.get_json(silent=True) or {}
        user_text = data.get("text", "").strip() or "跟我聊聊天"
        response = model.generate_content(user_text)
        return jsonify({"reply": response.text})
    except Exception as e:
        return jsonify({"reply": f"❌ Gemini 出錯：{str(e)}"})


if __name__ == "__main__":
    app.run(port=5000, debug=True, threaded=True)
