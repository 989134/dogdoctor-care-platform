"""Local operator recovery; requires access to the computer, no public reset endpoint."""
import getpass
import sqlite3
import time
from pathlib import Path
from werkzeug.security import generate_password_hash

if __name__=='__main__':
    path=Path(__file__).parent/'guardian_data/portal.sqlite3'
    if not path.exists(): raise SystemExit('尚未建立帳號資料庫')
    name=input('要復原的帳號：').strip()
    with sqlite3.connect(path) as db:
        if not db.execute('SELECT 1 FROM users WHERE name=?',(name,)).fetchone(): raise SystemExit('帳號不存在')
        password=getpass.getpass('新密碼（至少 12 字元，不顯示輸入）：')
        if not 12<=len(password)<=256: raise SystemExit('密碼長度不符')
        if password!=getpass.getpass('再次輸入新密碼：'): raise SystemExit('兩次密碼不相符')
        db.execute('UPDATE users SET password=? WHERE name=?',(generate_password_hash(password),name))
        db.execute('INSERT INTO audit(user,action,target,at) VALUES(?,?,?,?)',('local_operator','password_recovery',name,time.time()))
    print('密碼已重設，該帳號原有登入會失效。')
