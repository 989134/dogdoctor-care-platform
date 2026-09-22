import tempfile
import unittest
from pathlib import Path

from mec_edge_client import EventOutbox, percentile
from mec_event_sink import LocalJsonlEventSink


class MecEdgeClientTests(unittest.TestCase):
    def test_percentiles(self):
        self.assertEqual(percentile([1,2,3,4,5], .5), 3)
        self.assertEqual(percentile([], .95), None)

    def test_event_outbox_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox=EventOutbox(Path(directory)/'events.sqlite3')
            event={'event_id':'ranger-001-fall-1','kind':'fall'}
            self.assertTrue(outbox.add(event))
            self.assertFalse(outbox.add(event))
            self.assertEqual(outbox.count(),1)
            pending=outbox.pending()
            self.assertEqual(pending[0][0],event['event_id'])
            outbox.mark_failed(event['event_id'],'offline',now=100)
            self.assertFalse(outbox.pending(now=101))
            self.assertTrue(outbox.pending(now=103))
            outbox.mark_delivered(event['event_id'])
            self.assertEqual(outbox.count(),0)
            outbox.close()

    def test_local_sink_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events.jsonl';sink=LocalJsonlEventSink(path)
            event={'event_id':'event-1','type':'suspected_fall'}
            self.assertFalse(sink.upload_event(event)['duplicate'])
            self.assertTrue(sink.upload_event(event)['duplicate'])
            self.assertEqual(len(path.read_text(encoding='utf-8').splitlines()),1)


if __name__ == '__main__':
    unittest.main()
