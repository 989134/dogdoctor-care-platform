import tempfile
import unittest
from pathlib import Path

from flask import Flask

import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'eldertest'))
from care_web import create_blueprint
from medication_calendar import MedicationCalendar


class ServiceStub:
    def snapshot(self):
        return {'events': [], 'stream_stale': True}


class AudioStub:
    def __init__(self): self.spoken=[]
    def speak(self,text,priority):
        self.spoken.append((text,priority))
        return {'ok':True}


class MedicationCalendarTests(unittest.TestCase):
    def test_calendar_validates_and_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'medications.json'
            calendar=MedicationCalendar(path)
            self.assertEqual(len(calendar.list()),3)
            entry=calendar.add('飯後藥','09:30',[0,2,4],'飯後服用')
            self.assertEqual(MedicationCalendar(path).get(entry['id'])['days'],[0,2,4])
            with self.assertRaises(ValueError):calendar.add('藥物','25:00',[0])
            self.assertTrue(calendar.remove(entry['id']))

    def test_web_mutation_is_local_and_reminder_uses_robot_speaker(self):
        with tempfile.TemporaryDirectory() as temp:
            audio=AudioStub();app=Flask(__name__)
            app.register_blueprint(create_blueprint(ServiceStub(),audio_client=audio,
                                    medication_path=Path(temp)/'medications.json'))
            client=app.test_client()
            self.assertEqual(client.get('/care/medications').status_code,200)
            payload={'name':'晚餐後藥','time':'19:00','days':[0,1,2,3,4,5,6]}
            self.assertEqual(client.post('/care/medications',json=payload).status_code,403)
            response=client.post('/care/medications',json=payload,
                                 headers={'X-Care-Medication':'1'})
            self.assertEqual(response.status_code,200)
            ident=response.json['medication']['id']
            response=client.post(f'/care/medications/{ident}/remind',json={},
                                 headers={'X-Care-Medication':'1'})
            self.assertEqual(response.status_code,200)
            self.assertIn('晚餐後藥',audio.spoken[0][0])


if __name__=='__main__':unittest.main()
