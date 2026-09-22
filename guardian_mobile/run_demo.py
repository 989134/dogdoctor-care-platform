"""Local interactive demo using isolated, persistent sample data."""
import sqlite3
import time
from pathlib import Path

from werkzeug.security import generate_password_hash
from guardian.server import create_app

ROOT = Path(__file__).resolve().parent


def create_demo():
    data = ROOT / 'demo_data'
    source = data / 'events'
    source.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source / 'demo.sqlite3') as db:
        db.execute('''CREATE TABLE IF NOT EXISTS care_events(
            event_uid TEXT PRIMARY KEY,kind TEXT,status TEXT,risk_level TEXT,
            created_wall REAL,updated_wall REAL,snapshot_path TEXT)''')
        now = time.time()
        db.executemany('INSERT OR IGNORE INTO care_events VALUES(?,?,?,?,?,?,?)', [
            ('demo-fall', 'fall', 'active', 'high', now-900, now-900, ''),
            ('demo-inactivity', 'inactivity', 'review', 'medium', now-3600, now-3600, ''),
            ('demo-resolved', 'fall', 'resolved', 'low', now-86400, now-7200, ''),
        ])
    app = create_app(data / 'portal', source)
    db = app.extensions['guardian_db']
    db.execute('INSERT OR IGNORE INTO users VALUES(?,?,?)',
               ('demo', generate_password_hash('Demo-care-2026!'), 'admin'))
    db.commit()
    original_page = app.view_functions['page']

    def demo_page():
        return (original_page()
                .replace('家庭照護空間', 'DEMO · 示範資料')
                .replace('讓每一份照護，都有安心的依靠。', '互動展示：事件為示範資料，藥袋辨識使用實際服務。')
                .replace('<input id="name"', '<input value="demo" id="name"')
                .replace('<input id="password"', '<input value="Demo-care-2026!" id="password"')
                .replace('<h2>登入</h2>', '<h2>進入互動 Demo</h2><p>示範帳號已填入，按下登入即可操作。變更只儲存在獨立的 Demo 資料中。</p>'))

    app.view_functions['page'] = demo_page
    return app


if __name__ == '__main__':
    create_demo().run(host='127.0.0.1', port=5017, debug=False, use_reloader=False)
