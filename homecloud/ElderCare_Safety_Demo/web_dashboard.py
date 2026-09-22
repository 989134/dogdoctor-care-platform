from flask import Flask, render_template
from flask_socketio import SocketIO, emit
# 設定路由，發送骨骼數據到前端