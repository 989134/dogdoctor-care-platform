import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'eldertest'))
from audio_guardian import AudioGuardian


class AudioGuardianCareResponseTests(unittest.TestCase):
    def make_guardian(self):
        self.responses=[]
        self.alerts=[]
        return AudioGuardian(threading.Event(),{},lambda command: None,
                             self.alerts.append,
                             lambda response: self.responses.append(response) or True)

    def test_ok_phrase_cancels_active_fall(self):
        guardian=self.make_guardian()
        guardian._handle_voice_text('我沒事，不用幫忙')
        self.assertEqual(self.responses,['ok'])
        self.assertEqual(guardian.status['audio']['last_command'],'CARE_OK')

    def test_help_phrase_escalates_active_fall(self):
        guardian=self.make_guardian()
        guardian._handle_voice_text('好痛，我不能起來')
        self.assertEqual(self.responses,['help'])
        self.assertEqual(guardian.status['audio']['last_command'],'CARE_HELP')
        self.assertFalse(self.alerts)


if __name__=='__main__':
    unittest.main()
