"""Local status and cancellation UI. Importing does not start hardware or notifications."""
import json
import time
from pathlib import Path
from flask import Blueprint, Response, jsonify, request, send_file
from care_privacy import skeleton_required, POLICY
from medication_calendar import MedicationCalendar


ANNOUNCEMENTS = {
    'medication': '現在是服藥時間，請依照藥袋與醫囑服藥。服藥後記得喝水。',
    'water': '溫馨提醒，請記得補充水分。',
    'family': '家人正在透過銀髮守護犬和你聯絡，請靠近機器人。',
    'emergency': '請保持冷靜並留在安全的位置，家人已收到通知，協助正在前往。',
}


def create_blueprint(service, edge_status_path=None, assistant=None, audio_client=None,
                     medication_path=None, env_path=None):
    bp = Blueprint('care', __name__)
    edge_status_path = Path(edge_status_path) if edge_status_path else (
        Path(__file__).resolve().parents[1]/'mec_output'/'edge_status.json')
    medication_path = Path(medication_path) if medication_path else (
        Path(__file__).resolve().parents[1]/'care_medications.json')
    env_path = Path(env_path) if env_path else Path(__file__).resolve().parents[1]/'.env'
    medications = MedicationCalendar(medication_path)

    def update_env_value(name, value):
        lines=env_path.read_text(encoding='utf-8').splitlines() if env_path.exists() else []
        updated=[];replaced=False
        for line in lines:
            if line.strip().startswith(name+'='):
                updated.append(f'{name}={value}');replaced=True
            else:
                updated.append(line)
        if not replaced:updated.append(f'{name}={value}')
        env_path.parent.mkdir(parents=True,exist_ok=True)
        temporary=env_path.with_suffix(env_path.suffix+'.tmp')
        temporary.write_text('\n'.join(updated).rstrip()+'\n',encoding='utf-8')
        temporary.replace(env_path)

    @bp.get('/care')
    def page():
        return Path(__file__).with_name('care_dashboard.html').read_text(encoding='utf-8')

    @bp.get('/care/status')
    def status():
        return jsonify(service.snapshot())

    def local_medication_request():
        return (request.remote_addr in ('127.0.0.1','::1') and
                request.headers.get('X-Care-Medication') == '1')

    @bp.get('/care/medications')
    def medication_list():
        return jsonify(ok=True,medications=medications.list())

    @bp.post('/care/medications')
    def medication_add():
        if not local_medication_request():
            return jsonify(ok=False,error='請在這台 Windows 電腦的本機照護頁操作'),403
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        data=request.get_json(silent=True) or {}
        try:
            entry=medications.add(data.get('name'),data.get('time'),data.get('days'),
                                  data.get('note',''))
            return jsonify(ok=True,medication=entry,message='已加入吃藥行事曆')
        except ValueError as exc:
            return jsonify(ok=False,error=str(exc)),400

    @bp.delete('/care/medications/<ident>')
    def medication_remove(ident):
        if not local_medication_request():
            return jsonify(ok=False,error='請在這台 Windows 電腦的本機照護頁操作'),403
        if len(ident)!=32 or any(ch not in '0123456789abcdef' for ch in ident.lower()):
            return jsonify(ok=False,error='無效的行事曆項目'),400
        if not medications.remove(ident):
            return jsonify(ok=False,error='找不到行事曆項目'),404
        return jsonify(ok=True,message='已從吃藥行事曆移除')

    @bp.post('/care/medications/<ident>/remind')
    def medication_remind(ident):
        if not local_medication_request():
            return jsonify(ok=False,error='請在這台 Windows 電腦的本機照護頁操作'),403
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        entry=medications.get(ident)
        if entry is None:
            return jsonify(ok=False,error='找不到行事曆項目'),404
        if audio_client is None:
            return jsonify(ok=False,error='尚未設定車上音訊服務'),503
        text=f"現在是服藥時間，請依照藥袋與醫囑服用{entry['name']}。服藥後記得喝水。"
        try:
            result=audio_client.speak(text,'normal')
            return jsonify(ok=True,message='吃藥提醒已送到車上喇叭',robot=result)
        except Exception as exc:
            return jsonify(ok=False,error=str(exc)),503

    def local_follow_runtime():
        if request.remote_addr not in ('127.0.0.1','::1') or request.headers.get('X-Care-Follow')!='1':
            return None, (jsonify(ok=False,error='請在這台 Windows 電腦的本機照護頁操作'),403)
        runtime=getattr(service,'follow',None)
        if runtime is None:
            return None, (jsonify(ok=False,error='自主跟隨服務尚未啟動'),503)
        return runtime, None

    @bp.post('/care/follow/lock')
    def follow_lock():
        runtime,error=local_follow_runtime()
        if error:return error
        if not request.is_json:return jsonify(ok=False,error='需要 JSON 請求'),415
        data=request.get_json(silent=True) or {}
        try:
            runtime.request_lock(float(data.get('x')),float(data.get('y')))
            return jsonify(ok=True,message='目前不需鎖定人物，請直接按「開始跟隨」')
        except (TypeError,ValueError) as exc:
            return jsonify(ok=False,error=str(exc)),400

    @bp.post('/care/follow/enable')
    def follow_enable():
        runtime,error=local_follow_runtime()
        if error:return error
        try:
            runtime.enable()
            return jsonify(ok=True,message='人物前進模式已啟用；看到任何人就會向前')
        except Exception as exc:
            return jsonify(ok=False,error=str(exc)),409

    @bp.post('/care/follow/stop')
    def follow_stop():
        runtime,error=local_follow_runtime()
        if error:return error
        runtime.disable()
        return jsonify(ok=True,message='已停止人物前進模式並傳送停車指令')

    @bp.post('/care/follow/clear')
    def follow_clear():
        runtime,error=local_follow_runtime()
        if error:return error
        runtime.clear_target()
        return jsonify(ok=True,message='已停止人物前進模式')

    @bp.route('/care/privacy',methods=['GET','POST'])
    def privacy():
        if request.method=='POST':
            # This dashboard has no accounts: permit only explicit local UI calls.
            if request.remote_addr not in ('127.0.0.1','::1') or request.headers.get('X-Care-Privacy')!='1':
                return jsonify(error='請在電腦本機照護頁面操作'),403
            if not request.is_json:return jsonify(error='需要 JSON'),415
            mode=(request.get_json(silent=True) or {}).get('mode')
            if mode not in ('skeleton','manual_video'):return jsonify(error='無效模式'),400
            POLICY.parent.mkdir(parents=True,exist_ok=True)
            temp=POLICY.with_suffix('.tmp')
            temp.write_text(json.dumps(dict(mode=mode,updated=time.time())),encoding='utf-8')
            temp.replace(POLICY)
            with service.lock:
                service.jpeg=None
                service.evidence_frames.clear()
        private=skeleton_required()
        return jsonify(ok=True,skeleton=private,
                       mode='skeleton' if private else 'manual_video')

    @bp.get('/care/audio/status')
    def audio_status():
        robot = audio_client.health() if audio_client is not None else {
            'ok': False, 'connected': False, 'error': '尚未設定 ROBOT_AUDIO_URL'
        }
        ai = assistant.status() if assistant is not None else {
            'configured': False, 'error': '陪伴助手未啟動'
        }
        return jsonify(ok=True, robot=robot, assistant=ai)

    @bp.route('/care/assistant/settings',methods=['GET','POST'])
    def assistant_settings():
        if request.remote_addr not in ('127.0.0.1','::1'):
            return jsonify(ok=False,error='請在這台 Windows 電腦的本機照護頁操作'),403
        if request.method=='GET':
            status=(assistant.status() if assistant is not None else
                    {'configured':False,'error':'陪伴助手未啟動'})
            return jsonify(ok=True,assistant=status)
        if request.headers.get('X-Care-Settings')!='1':
            return jsonify(ok=False,error='缺少本機設定驗證'),403
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        if assistant is None:
            return jsonify(ok=False,error='陪伴助手未啟動'),503
        api_key=str((request.get_json(silent=True) or {}).get('api_key','')).strip()
        try:
            status=assistant.update_api_key(api_key)
            update_env_value('GEMINI_API_KEY',api_key)
        except ValueError as exc:
            return jsonify(ok=False,error=str(exc)),400
        except OSError:
            return jsonify(ok=False,error='無法保存 API Key，請檢查 .env 寫入權限'),500
        return jsonify(ok=True,message='新的 Gemini API Key 已保存並立即套用',
                       assistant=status)

    @bp.post('/care/audio/speak')
    def audio_speak():
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        if audio_client is None:
            return jsonify(ok=False,error='尚未設定車上音訊服務'),503
        data=request.get_json(silent=True) or {}
        text=' '.join(str(data.get('text','')).split())
        priority=str(data.get('priority','normal'))
        if not text:
            return jsonify(ok=False,error='請輸入要讓車子說的內容'),400
        if len(text)>300:
            return jsonify(ok=False,error='語音內容不可超過 300 個字'),400
        try:
            result=audio_client.speak(text,priority)
            return jsonify(ok=True,message='內容已送到車上喇叭',robot=result)
        except Exception as exc:
            return jsonify(ok=False,error=str(exc)),503

    @bp.post('/care/audio/announce')
    def audio_announce():
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        data=request.get_json(silent=True) or {}
        kind=str(data.get('kind',''))
        text=ANNOUNCEMENTS.get(kind)
        if text is None:
            return jsonify(ok=False,error='未知的提醒類型'),400
        if audio_client is None:
            return jsonify(ok=False,error='尚未設定車上音訊服務'),503
        try:
            result=audio_client.speak(text,'emergency' if kind=='emergency' else 'normal')
            return jsonify(ok=True,message='提醒已送到車上喇叭',text=text,robot=result)
        except Exception as exc:
            return jsonify(ok=False,error=str(exc)),503

    @bp.post('/care/audio/stop')
    def audio_stop():
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        if audio_client is None:
            return jsonify(ok=False,error='尚未設定車上音訊服務'),503
        try:
            return jsonify(ok=True,robot=audio_client.stop(),message='已要求停止車上語音')
        except Exception as exc:
            return jsonify(ok=False,error=str(exc)),503

    @bp.post('/care/audio/microphone-test')
    def audio_microphone_test():
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        if audio_client is None:
            return jsonify(ok=False,error='尚未設定車上音訊服務'),503
        try:
            result=audio_client.microphone_test(2.0)
            return jsonify(ok=True,message='車上麥克風測試完成',robot=result)
        except Exception as exc:
            return jsonify(ok=False,error=str(exc)),503

    @bp.post('/care/assistant')
    def companion_chat():
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        data=request.get_json(silent=True) or {}
        text=str(data.get('text','')).strip()
        speak_on_robot=bool(data.get('speak_on_robot',True))
        if assistant is None:
            return jsonify(ok=False,error='陪伴助手未啟動'),503
        try:
            reply=assistant.ask(text)
        except (ValueError,RuntimeError) as exc:
            return jsonify(ok=False,error=str(exc)),400 if isinstance(exc,ValueError) else 503
        speaker={'requested':speak_on_robot,'sent':False}
        if speak_on_robot:
            if audio_client is None:
                speaker['error']='尚未設定車上音訊服務'
            else:
                try:
                    speaker['robot']=audio_client.speak(reply,'normal')
                    speaker['sent']=True
                except Exception as exc:
                    speaker['error']=str(exc)
        return jsonify(ok=True,reply=reply,speaker=speaker)

    @bp.post('/care/assistant/voice-turn')
    def companion_voice_turn():
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        if assistant is None:
            return jsonify(ok=False,error='陪伴助手未啟動'),503
        if audio_client is None:
            return jsonify(ok=False,error='尚未設定車上音訊服務'),503
        data=request.get_json(silent=True) or {}
        try:
            duration=max(3.0,min(10.0,float(data.get('duration',6.0))))
            captured=audio_client.capture_audio(duration)
            if not captured.get('heard'):
                return jsonify(ok=False,error='沒有收到清楚聲音，請靠近車上麥克風再說一次',
                               audio={k:v for k,v in captured.items() if k!='wav'}),422
            result=assistant.ask_audio(captured['wav'])
            speaker=audio_client.speak(result['reply'],'normal')
            return jsonify(ok=True,transcript=result['transcript'],reply=result['reply'],
                           speaker=speaker,
                           audio={k:v for k,v in captured.items() if k!='wav'})
        except (ValueError,RuntimeError) as exc:
            return jsonify(ok=False,error=str(exc)),400 if isinstance(exc,ValueError) else 503
        except Exception as exc:
            return jsonify(ok=False,error=str(exc)),503

    @bp.post('/care/assistant/reset')
    def companion_reset():
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        if assistant is None:
            return jsonify(ok=False,error='陪伴助手未啟動'),503
        assistant.clear_history()
        return jsonify(ok=True,message='陪伴對話紀錄已清除')

    @bp.get('/care/edge-status')
    def edge_status():
        try:
            data=json.loads(edge_status_path.read_text(encoding='utf-8'))
            age=max(0,time.time()-float(data.get('updated_at',0)))
            data['status_age_seconds']=round(age,1)
            data['status_stale']=age>10
            if data['status_stale']:data['mode']='agent_stopped'
            return jsonify(data)
        except (OSError,ValueError,TypeError,json.JSONDecodeError):
            return jsonify(mode='not_started',status_stale=True,
                           message='Edge Agent 尚未啟動'),200

    @bp.post('/care/events/<int:event_id>/cancel')
    def cancel(event_id):
        # JSON-only mutation prevents a third-party HTML form cancelling an alert.
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        ok = service.cancel(event_id)
        return jsonify(ok=ok), 200 if ok else 404

    @bp.post('/care/fall-response')
    def fall_response():
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        response = (request.get_json(silent=True) or {}).get('response')
        if response not in ('ok','help'):
            return jsonify(ok=False,error='response 必須是 ok 或 help'),400
        ok = service.respond_to_fall(response)
        message = ('已取消跌倒警報' if response == 'ok' else '已立即升級高風險警報')
        return jsonify(ok=ok,message=message if ok else '目前沒有等待處理的跌倒事件'), 200 if ok else 404

    @bp.get('/care/events/history')
    def event_history():
        try:
            limit=int(request.args.get('limit','100'))
        except ValueError:
            return jsonify(ok=False,error='limit 必須是整數'),400
        return jsonify(events=service.event_history(limit))

    @bp.get('/care/events/<event_uid>/snapshot')
    def event_snapshot(event_uid):
        if len(event_uid)!=32 or any(ch not in '0123456789abcdef' for ch in event_uid.lower()):
            return jsonify(ok=False,error='無效的事件識別碼'),400
        path=service.event_snapshot(event_uid)
        if path is None:
            return jsonify(ok=False,error='沒有警報截圖'),404
        return send_file(path,mimetype='image/jpeg',conditional=True)

    @bp.post('/care/rest-zone')
    def set_rest_zone():
        if not request.is_json:
            return jsonify(ok=False, message='需要 JSON'), 415
        data = request.get_json(silent=True) or {}
        try:
            zone = service.set_rest_zone([data[name] for name in ('x1','y1','x2','y2')])
        except (KeyError, TypeError, ValueError):
            return jsonify(ok=False, message='休息區座標必須介於 0 到 1，且左上小於右下'), 400
        return jsonify(ok=True, zone=zone, message='休息區已保存')

    @bp.post('/care/rest-zones/clear')
    def clear_rest_zones():
        if not request.is_json:
            return jsonify(ok=False, message='需要 JSON'), 415
        service.clear_rest_zones()
        return jsonify(ok=True, message='休息區已清除')

    @bp.post('/care/environment/baseline')
    def environment_baseline():
        if not request.is_json:
            return jsonify(ok=False,error='需要 JSON 請求'),415
        ok, message = service.set_environment_baseline()
        return jsonify(ok=ok,message=message), 200 if ok else 409

    @bp.get('/care/video_feed')
    def video():
        def frames():
            while not service.stop_event.is_set():
                with service.lock:
                    jpeg = service.jpeg
                    if skeleton_required() and not getattr(service,'jpeg_private',False):
                        jpeg = None
                if jpeg:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'+jpeg+b'\r\n'
                time.sleep(0.1)
        return Response(frames(),mimetype='multipart/x-mixed-replace; boundary=frame')
    return bp
