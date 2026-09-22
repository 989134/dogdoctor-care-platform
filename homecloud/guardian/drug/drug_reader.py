"""藥盒／鋁箔包文字辨識。執行本檔開啟視窗；--self-test 驗證資料。"""
from pathlib import Path
import sys
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / '.packages'))
import json
import re
import threading
import queue
from collections import defaultdict, Counter
import unicodedata
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

def norm(text):
    return re.sub(r'[^A-Z0-9\u4e00-\u9fff]', '', unicodedata.normalize('NFKC', str(text)).upper())

STOP = set('TABLETS TABLET CAPSULES CAPSULE INJECTION FILM COATED GRANULES GRANULE EFFERVESCENT CREAM SYRUP SOLUTION MG ML GM USP MADE TAIWAN EXP LOT'.split())

def strengths(text):
    text = unicodedata.normalize('NFKC', text).upper()
    for old, new in [('毫克', 'MG'), ('毫升', 'ML'), ('公克', 'G'), ('微克', 'MCG')]:
        text = text.replace(old, new)
    text = re.sub(r'\s+', '', text).replace('GM', 'G')
    return set(re.findall(r'\d+(?:\.\d+)?(?:MCG|MG)(?:/(?:\d+(?:\.\d+)?)?(?:ML|G))?|\d+(?:\.\d+)?%', text))

def keywords(text):
    text = unicodedata.normalize('NFKC', text).upper()
    words = set(re.findall(r'[A-Z]{4,}', text)) - STOP
    for part in re.findall(r'[\u4e00-\u9fff]+', text):
        words.update(part[i:i+3] for i in range(len(part)-2))
    return words

def license_key(text):
    match = re.search(r'(衛[署部](?:藥|菌疫)(?:製|輸|陸輸)字)(?:第)?(\d{6})(?:號)?', norm(text))
    return ''.join(match.groups()) if match else ''

def default_sources():
    xml = BASE / '36_1.xml' / '36_1.xml'
    xlsx = BASE / '11508公告_9月份健保用藥項目價格檔異動.xlsx'
    return xml, xlsx

class Catalog:
    def __init__(self, xml, xlsx):
        import openpyxl
        self.drugs = []
        context = ET.iterparse(xml, events=('start', 'end'))
        _, root = next(context)
        for event, elem in context:
            if event == 'end' and elem.tag == 'rows':
                data = {c.tag: ''.join(c.itertext()).strip() for c in elem}
                names = [norm(data.get(k, '')) for k in ('中文品名', '英文品名')]
                self.drugs.append((data, names, norm(data.get('許可證字號', ''))))
                elem.clear()
                root.clear()
        if not self.drugs:
            raise ValueError('XML 中找不到 rows 藥品資料。')
        self.index = defaultdict(set)
        for i, (record, _, _) in enumerate(self.drugs):
            for token in keywords(record.get('中文品名', '') + ' ' + record.get('英文品名', '')):
                self.index[token].add(i)
        self.nhi = []
        self.warning = ''
        if not Path(xlsx).is_file():
            self.warning = '健保檔不存在，已保留許可證辨識功能。'
            return
        workbook = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
        try:
            for sheet in workbook:
                headers = None
                for row in sheet.values:
                    if '健保代碼' in row and '藥品名稱' in row:
                        headers = [str(v).strip() if v is not None else '' for v in row]
                        continue
                    if headers:
                        record = {k: str(v).replace('_x000D_', '').strip() if v is not None else '' for k, v in zip(headers, row) if k}
                        if re.fullmatch(r'[A-Z0-9]{10}', record.get('健保代碼', '')):
                            self.nhi.append(record)
        finally:
            workbook.close()
        if not self.nhi:
            raise ValueError('Excel 找不到健保代碼／藥品名稱欄位或有效資料。')

    def candidates(self, text):
        from rapidfuzz.fuzz import ratio
        query = norm(text)
        license_query = license_key(text)
        if len(query) < 3:
            return []
        lines = [norm(s) for s in text.splitlines() if len(norm(s)) >= 3]
        hits = Counter()
        for token in keywords(text):
            ids = self.index.get(token, ())
            if 0 < len(ids) <= 180:
                for i in ids:
                    hits[i] += 1
        results = []
        for index, (data, names, license_no) in enumerate(self.drugs):
            score, evidence = 0, ''
            if index in hits:
                score, evidence = min(85, 72 + hits[index] * 3), '藥名關鍵字相符（部分文字），請核對含量與廠商'
            if license_no and (license_no in query or (license_query and license_query == license_key(license_no))):
                score, evidence = 100, '完整許可證字號相符'
            for name in names:
                if len(name) < 3:
                    continue
                if name in query:
                    value, reason = 96, '完整品名出現在輸入文字'
                elif any(len(line) >= 4 and line in name for line in lines):
                    value, reason = 78, '品名包含查詢文字'
                else:
                    value = max((ratio(line, name) for line in lines), default=0) * 0.88
                    reason = '品名文字近似，須核對規格與廠商'
                if value > score:
                    score, evidence = value, reason
            if score >= 64:
                results.append((round(score, 1), evidence, data))
        return sorted(results, key=lambda r: (r[0], not bool(r[2].get('註銷狀態'))), reverse=True)

    def search(self, text):
        """Only return a unique, well-supported result; ambiguity is not identification."""
        pool = self.candidates(text)
        requested = strengths(text)
        chosen = {}
        for score, evidence, data in pool:
            names = data.get('中文品名', '') + ' ' + data.get('英文品名', '')
            known = strengths(names)
            if requested and not requested.issubset(known):
                continue
            # Exact license or full product name is needed, not a common word.
            if score < 96:
                continue
            evidence += '；含量一致' if requested else ''
            key = data.get('許可證字號', '')
            if key not in chosen:
                chosen[key] = (score, evidence, dict(data))
            else:
                # One license may have several factory rows; retain their details.
                merged = chosen[key][2]
                for field, value in data.items():
                    if value and value not in merged.get(field, '').split(' ／ '):
                        merged[field] = ' ／ '.join(filter(None, [merged.get(field, ''), value]))
        ranked = sorted(chosen.values(), key=lambda row: row[0], reverse=True)
        if not ranked:
            return []
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 8:
            return []
        # A short complete name can also be a prefix of a different strength.
        if not license_key(text) and not requested:
            top_license = ranked[0][2].get('許可證字號')
            if any(r[0] >= 75 and r[2].get('許可證字號') != top_license for r in pool):
                return []
        return ranked[:1]

    def nhi_search(self, text):
        query = norm(text)
        return [r for r in self.nhi if norm(r['健保代碼']) in query or (len(norm(r['藥品名稱'])) >= 4 and norm(r['藥品名稱']) in query)]

def recognize(path, engine=None):
    from PIL import Image, ImageOps
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    engine = engine or RapidOCR()
    with Image.open(path) as source:
        pic = ImageOps.exif_transpose(source).convert('RGB')
        pic.thumbnail((2400, 2400))
        result, _ = engine(np.asarray(pic))
    return '\n'.join(row[1] for row in (result or [])), engine

class CameraWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title('相機拍照')
        self.geometry('760x610')
        self.stop = threading.Event()
        self.worker = None
        self.frames = queue.Queue(maxsize=1)
        self.errors = queue.Queue()
        self.current = None
        self.preview = ttk.Label(self, text='請選擇相機編號，再按啟動相機', anchor='center')
        self.preview.pack(fill='both', expand=True)
        controls = ttk.Frame(self, padding=10)
        controls.pack(fill='x')
        ttk.Label(controls, text='相機編號').pack(side='left')
        self.device = tk.StringVar(value='0')
        ttk.Combobox(controls, textvariable=self.device, values=['0', '1', '2', '3', '4'], width=4, state='readonly').pack(side='left')
        ttk.Button(controls, text='啟動相機', command=self.start).pack(side='left', padx=8)
        self.snap = ttk.Button(controls, text='拍照並辨識', command=self.capture, state='disabled')
        self.snap.pack(side='left')
        ttk.Button(controls, text='關閉', command=self.close).pack(side='right')
        self.message = tk.StringVar(value='內建相機通常為 0；USB 相機可嘗試 1 或 2。')
        ttk.Label(self, textvariable=self.message, wraplength=700, padding=10).pack(fill='x')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(70, self.poll)

    def start(self):
        if self.worker and self.worker.is_alive():
            self.message.set('相機正在執行。若要換相機，請關閉此視窗後重新開啟。')
            return
        index = int(self.device.get())
        self.current = None
        self.snap.state(['disabled'])
        self.message.set('正在開啟相機…')
        def run():
            import cv2
            cap = None
            try:
                for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF):
                    if self.stop.is_set():
                        return
                    cap = cv2.VideoCapture(index, backend)
                    if cap.isOpened():
                        break
                    cap.release()
                if cap is None or not cap.isOpened():
                    raise RuntimeError('無法開啟相機。請確認已連接、編號正確，關閉其他使用相機的程式，並在 Windows 設定允許桌面應用程式存取相機。')
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                failures = 0
                while not self.stop.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        failures += 1
                        if failures > 20:
                            raise RuntimeError('相機無法取得影像，請檢查連線或更換相機編號。')
                        self.stop.wait(.05)
                        continue
                    failures = 0
                    if self.frames.full():
                        try:
                            self.frames.get_nowait()
                        except queue.Empty:
                            pass
                    self.frames.put_nowait(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    self.stop.wait(.03)
            except Exception as exc:
                self.errors.put(str(exc))
            finally:
                if cap is not None:
                    cap.release()
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def poll(self):
        if self.stop.is_set():
            return
        from PIL import Image, ImageTk
        try:
            frame = self.frames.get_nowait()
            self.current = Image.fromarray(frame)
            thumb = self.current.copy()
            thumb.thumbnail((720, 450))
            self.photo = ImageTk.PhotoImage(thumb)
            self.preview.configure(image=self.photo, text='')
            self.snap.state(['!disabled'])
            self.message.set('請將藥名與許可證對準鏡頭，待畫面清楚後拍照。')
        except queue.Empty:
            pass
        try:
            self.message.set(self.errors.get_nowait())
            self.current = None
            self.snap.state(['disabled'])
        except queue.Empty:
            pass
        self.after(70, self.poll)

    def capture(self):
        if self.current is not None:
            image = self.current.copy()
            self.close()
            self.app.save_capture(image)

    def close(self):
        self.stop.set()
        self.destroy()

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('藥盒與鋁箔包辨識')
        self.geometry('1180x820')
        self.minsize(900, 650)
        self.catalog = None
        self.engine = None
        self.events = queue.Queue()
        self.results = []
        self.photo_path = None
        self.busy = False
        self.pending_photo = False
        self.camera = None
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft JhengHei', 10))
        panel = ttk.Frame(self, padding=14)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='藥盒／鋁箔包辨識', font=('Microsoft JhengHei', 22, 'bold')).pack(anchor='w')
        ttk.Label(panel, text='單筆辨識：核對完整藥名與含量，或完整許可證。無法唯一判定時請補拍。比對分數不是正確率。').pack(anchor='w', pady=5)
        self.paths = []
        for label, path in zip(('許可證 XML', '健保異動 XLSX'), default_sources()):
            row = ttk.Frame(panel)
            row.pack(fill='x', pady=2)
            ttk.Label(row, text=label, width=16).pack(side='left')
            var = tk.StringVar(value=str(path))
            self.paths.append(var)
            ttk.Entry(row, textvariable=var).pack(side='left', fill='x', expand=True)
            ttk.Button(row, text='選擇', command=lambda v=var: self.pick_source(v)).pack(side='left')
        toolbar = ttk.Frame(panel)
        toolbar.pack(fill='x', pady=8)
        self.buttons = []
        for label, action in [('條件搜尋', self.open_search), ('重新載入資料', self.load), ('選擇照片', self.open_photo), ('開啟相機', self.open_camera), ('貼上圖片', self.paste_photo), ('旋轉照片 90°', self.rotate), ('比對文字', self.search), ('匯出結果', self.export)]:
            button = ttk.Button(toolbar, text=label, command=action)
            button.pack(side='left', padx=3)
            if label not in ('選擇照片', '開啟相機', '貼上圖片'):
                self.buttons.append(button)
        pathrow = ttk.Frame(panel)
        pathrow.pack(fill='x', pady=4)
        self.image_path = tk.StringVar()
        ttk.Label(pathrow, text='照片路徑').pack(side='left')
        ttk.Entry(pathrow, textvariable=self.image_path).pack(side='left', fill='x', expand=True)
        ttk.Button(pathrow, text='載入此路徑', command=lambda: self.accept_photo(self.image_path.get().strip().strip('"'))).pack(side='left')
        upper = ttk.Frame(panel)
        upper.pack(fill='x')
        self.preview = ttk.Label(upper, text='請載入清楚、無反光的包裝照片', anchor='center', width=35)
        self.preview.pack(side='left', padx=8)
        textpanel = ttk.Frame(upper)
        textpanel.pack(side='left', fill='both', expand=True)
        ttk.Label(textpanel, text='辨識文字（可修正，或直接輸入藥名／完整許可證／健保代碼）').pack(anchor='w')
        self.input = tk.Text(textpanel, height=8, wrap='word', font=('Microsoft JhengHei', 12))
        self.input.pack(fill='both', expand=True)
        self.status = tk.StringVar(value='準備載入資料…')
        ttk.Label(panel, textvariable=self.status, wraplength=1100).pack(anchor='w', pady=8)
        self.table = ttk.Treeview(panel, columns=('score', 'name', 'license', 'state'), show='headings', height=8)
        for key, label, width in [('score', '比對分數', 85), ('name', '候選藥品', 470), ('license', '許可證字號', 210), ('state', '資料註銷狀態', 120)]:
            self.table.heading(key, text=label)
            self.table.column(key, width=width)
        self.table.pack(fill='both', expand=True)
        self.table.bind('<<TreeviewSelect>>', self.details)
        self.output = tk.Text(panel, height=11, wrap='word', font=('Microsoft JhengHei', 10))
        self.output.pack(fill='both', expand=True, pady=8)
        ttk.Label(panel, text='僅供資料查詢，請由藥師確認。健保異動檔不是完整給付清單；未找到不代表未納入健保。').pack(anchor='w')
        self.after(100, self.poll)
        self.after(200, self.load)
        self.protocol('WM_DELETE_WINDOW', self.close_app)

    def close_app(self):
        if self.camera:
            self.camera.close()
        self.destroy()

    def open_search(self):
        if self.catalog is None:
            messagebox.showinfo('尚未載入', '請先載入許可證資料。', parent=self)
            return
        from drug_search_ui import SearchWindow
        window = getattr(self, 'search_window', None)
        if window is not None and window.winfo_exists():
            window.lift()
        else:
            self.search_window = SearchWindow(self)

    def pick_source(self, var):
        path = filedialog.askopenfilename(parent=self)
        if path:
            var.set(path)

    def work(self, action, done, label):
        if self.busy:
            return
        self.busy = True
        self.status.set(label)
        for b in self.buttons:
            b.state(['disabled'])
        def run():
            try:
                self.events.put((done, action(), None))
            except Exception as exc:
                self.events.put((done, None, str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        try:
            done, value, error = self.events.get_nowait()
            self.busy = False
            for b in self.buttons:
                b.state(['!disabled'])
            if error:
                self.status.set('操作失敗：' + error)
                messagebox.showerror('無法完成', error)
            else:
                try:
                    done(value)
                except Exception as exc:
                    self.status.set('操作失敗：' + str(exc))
                    messagebox.showerror('操作失敗', str(exc), parent=self)
            if self.pending_photo and not self.busy:
                self.pending_photo = False
                self.ocr()
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def load(self):
        paths = [v.get() for v in self.paths]
        self.catalog = None
        self.results = []
        self.direct_nhi = []
        self.table.delete(*self.table.get_children())
        self.output.delete('1.0', 'end')
        def done(catalog):
            self.catalog = catalog
            self.status.set(f'已載入 {len(catalog.drugs):,} 筆許可證、{len(catalog.nhi):,} 筆健保異動。' + catalog.warning)
        self.work(lambda: Catalog(*paths), done, '正在讀取兩份資料…')

    def show_photo(self):
        from PIL import Image, ImageTk, ImageOps
        with Image.open(self.photo_path) as im:
            im = ImageOps.exif_transpose(im).convert('RGB')
            im.thumbnail((280, 180))
            self.photo = ImageTk.PhotoImage(im)
        self.preview.configure(image=self.photo, text='')

    def open_photo(self):
        path = filedialog.askopenfilename(parent=self, title='選擇藥盒或鋁箔包照片', filetypes=[('所有檔案', '*.*'), ('照片', ('*.png', '*.jpg', '*.jpeg', '*.bmp', '*.webp', '*.tif', '*.tiff'))])
        if not path:
            return
        self.accept_photo(path)

    def accept_photo(self, path):
        if not path:
            return
        old_path = self.photo_path
        self.photo_path = path
        try:
            self.show_photo()
        except Exception as exc:
            self.photo_path = old_path
            messagebox.showerror('照片無法讀取', f'{exc}\n請使用 JPG、PNG、BMP、WEBP 或 TIFF；HEIC 請先轉成 JPG。', parent=self)
            return
        self.image_path.set(path)
        self.results = []
        self.direct_nhi = []
        self.table.delete(*self.table.get_children())
        self.output.delete('1.0', 'end')
        if self.busy:
            self.pending_photo = True
            self.status.set('照片已選取，待目前工作完成後自動辨識。')
        else:
            self.ocr()

    def paste_photo(self):
        from PIL import ImageGrab, Image
        try:
            content = ImageGrab.grabclipboard()
            if isinstance(content, Image.Image):
                self.save_capture(content)
            elif isinstance(content, list) and content:
                self.accept_photo(content[0])
            else:
                messagebox.showinfo('剪貼簿沒有圖片', '請先複製照片，或用 Win+Shift+S 擷取包裝圖片。', parent=self)
        except Exception as exc:
            messagebox.showerror('貼上失敗', str(exc), parent=self)

    def save_capture(self, image):
        import uuid
        folder = BASE / 'captures'
        folder.mkdir(exist_ok=True)
        path = folder / (uuid.uuid4().hex + '.png')
        image.save(path)
        self.accept_photo(str(path))

    def open_camera(self):
        if self.camera and self.camera.winfo_exists():
            self.camera.lift()
        else:
            self.camera = CameraWindow(self)

    def rotate(self):
        if not self.photo_path:
            return
        from PIL import Image, ImageOps
        import tempfile
        with Image.open(self.photo_path) as im:
            rotated = ImageOps.exif_transpose(im).convert('RGB').rotate(90, expand=True)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            self.photo_path = tmp.name
        rotated.save(self.photo_path)
        self.show_photo()
        self.ocr()

    def ocr(self):
        path = self.photo_path
        def done(value):
            text, self.engine = value
            if self.pending_photo or path != self.photo_path:
                return
            self.input.delete('1.0', 'end')
            self.input.insert('1.0', text)
            if text:
                self.search()
            else:
                self.results = []
                self.table.delete(*self.table.get_children())
                self.output.delete('1.0', 'end')
                self.status.set('沒有讀到文字，請換照片、旋轉照片，或手動輸入。')
        self.work(lambda: recognize(path, self.engine), done, '正在本機辨識文字，首次初始化模型可能需要一些時間…')

    def search(self):
        if not self.catalog:
            messagebox.showinfo('尚未載入', '請先成功載入資料。')
            return
        text = self.input.get('1.0', 'end').strip()
        def done(value):
            if self.pending_photo:
                return
            self.results, self.direct_nhi = value
            self.table.delete(*self.table.get_children())
            self.output.delete('1.0', 'end')
            for i, (score, evidence, data) in enumerate(self.results):
                self.table.insert('', 'end', iid=str(i), values=(score, data.get('中文品名'), data.get('許可證字號'), data.get('註銷狀態') or '未標註銷'))
            self.status.set('已找到一筆相符結果，請核對包裝品名、含量與廠商。' if self.results else '無法唯一確認藥品：請補拍完整藥名、含量或許可證字號，再辨識。')
            if self.results:
                self.table.selection_set('0')
                self.details()
            else:
                self.output.insert('end', '目前文字不足、規格不一致，或有多筆同樣相符的藥品，因此不顯示猜測結果。\n請補拍包裝背面許可證，或保留完整藥名、劑型和含量再比對。\n\n' + self.nhi_text(self.direct_nhi))
        self.work(lambda: (self.catalog.search(text), self.catalog.nhi_search(text)), done, '正在比對藥品名稱…')

    def nhi_text(self, records):
        title = '健保異動資料（非完整清單；名稱相符不代表同一許可證或包裝，須核對）：\n'
        return title + ('\n\n'.join('\n'.join(f'{k}：{v}' for k, v in r.items()) for r in records) if records else '本檔未找到相符資料。')

    def details(self, event=None):
        selected = self.table.selection()
        if not selected:
            return
        score, evidence, data = self.results[int(selected[0])]
        records = [r for r in self.catalog.nhi if norm(r['藥品名稱']) in [norm(data.get('中文品名', '')), norm(data.get('英文品名', ''))]]
        self.output.delete('1.0', 'end')
        self.output.insert('end', f'比對依據：{evidence}\n\n' + '\n'.join(f'{k}：{v}' for k, v in data.items() if v) + '\n\n' + self.nhi_text(records))
        if getattr(self, 'direct_nhi', []):
            self.output.insert('end', '\n\n以下為輸入文字命中的異動，尚未連結至所選許可證：\n' + self.nhi_text(self.direct_nhi))

    def export(self):
        path = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('JSON', '*.json')])
        if path:
            try:
                Path(path).write_text(json.dumps({'輸入文字': self.input.get('1.0', 'end').strip(), '來源': [v.get() for v in self.paths], '候選': self.results, '輸入相符健保異動': getattr(self, 'direct_nhi', []), '說明': '分數非正確率，需人工核對'}, ensure_ascii=False, indent=2), encoding='utf-8')
                self.status.set('已匯出：' + path)
            except OSError as exc:
                messagebox.showerror('匯出失敗', str(exc))

if __name__ == '__main__':
    try:
        if '--self-test' in sys.argv:
            c = Catalog(*default_sources())
            assert len(c.drugs) > 10000 and len(c.nhi) == 253
            sample = c.drugs[0][0]
            assert c.search(sample['許可證字號'])[0][2]['許可證字號'] == sample['許可證字號']
            assert c.search(sample['中文品名'])
            assert not c.search('ZXQVVNONEXISTENT999999')
            assert not c.search('5')
            assert c.nhi_search(c.nhi[0]['健保代碼'])
            print(f'PASS: {len(c.drugs)} licenses; {len(c.nhi)} NHI changes; search checks passed.')
        else:
            App().mainloop()
    except Exception as exc:
        import traceback
        (BASE / 'drug_reader_error.log').write_text(traceback.format_exc(), encoding='utf-8')
        messagebox.showerror('啟動失敗', f'{exc}\n請用 launch.cmd 執行，詳細錯誤見 drug_reader_error.log。')
        raise
