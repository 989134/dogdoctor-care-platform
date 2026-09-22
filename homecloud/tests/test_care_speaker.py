import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'eldertest'))
from care_speaker import CareSpeaker, RobotCareSpeaker


class FakeEngine:
    def __init__(self):
        self.messages=[]
    def setProperty(self,*args):
        pass
    def say(self,text):
        self.messages.append(text)
    def runAndWait(self):
        pass


class CareSpeakerTests(unittest.TestCase):
    def test_question_and_alert_are_each_spoken_once(self):
        engine=FakeEngine();speaker=CareSpeaker(lambda:engine);speaker.start()
        event={'event_uid':'abc','id':1,'kind':'tracking_review','status':'review'}
        speaker.observe([event]);speaker.observe([event])
        speaker.observe([{**event,'status':'alert'}]);speaker.observe([{**event,'status':'alert'}])
        deadline=time.time()+1
        while len(engine.messages)<2 and time.time()<deadline:
            time.sleep(.01)
        speaker.close()
        self.assertEqual(len(engine.messages),2)
        self.assertIn('你還好嗎',engine.messages[0])
        self.assertIn('警報已升級',engine.messages[1])

    def test_robot_speaker_greeting_and_alerts_are_deduplicated(self):
        class FakeAudio:
            def __init__(self): self.messages=[]
            def speak(self,text,priority='normal'):
                self.messages.append((text,priority));return {'ok':True}
        audio=FakeAudio();speaker=RobotCareSpeaker(audio);speaker.start()
        speaker.arrival()
        event={'event_uid':'sit1','id':2,'kind':'prolonged_sitting','status':'alert'}
        speaker.observe([event]);speaker.observe([event])
        deadline=time.time()+1
        while len(audio.messages)<2 and time.time()<deadline:
            time.sleep(.01)
        speaker.close()
        self.assertEqual(len(audio.messages),2)
        self.assertIn('主人你今天好嗎',audio.messages[0][0])
        self.assertIn('連續坐姿已超過十秒',audio.messages[1][0])
        self.assertEqual(audio.messages[1][1],'emergency')


if __name__=='__main__':
    unittest.main()
