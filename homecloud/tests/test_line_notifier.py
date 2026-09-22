import sys
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'eldertest'))
from line_notifier import LineNotifier


class LineNotifierTests(unittest.TestCase):
    def test_missing_recipient_does_not_broadcast(self):
        notifier=LineNotifier();notifier.access_token='test';notifier.user_id=None
        with patch('line_notifier.requests.post') as post:
            self.assertFalse(notifier.send_message('test'))
            post.assert_not_called()

    def test_push_uses_only_configured_recipient(self):
        notifier=LineNotifier();notifier.access_token='test';notifier.user_id='family-user'
        response=Mock(status_code=200)
        with patch('line_notifier.requests.post',return_value=response) as post:
            self.assertTrue(notifier.send_message('care alert'))
        self.assertEqual(post.call_args.kwargs['json']['to'],'family-user')


if __name__=='__main__':
    unittest.main()
