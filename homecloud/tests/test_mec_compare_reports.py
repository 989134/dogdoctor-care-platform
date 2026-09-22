import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from mec_compare_reports import parse_spec,summarize,write_outputs


class MecReportComparisonTests(unittest.TestCase):
    def test_summary_and_three_output_formats(self):
        report={'frames_captured':10,'local_safety_frames':10,
                'remote':{'sent':9,'failed':1,'dropped':0,'state':'online'},
                'rtt_ms':{'p50':50,'p95':80,'p99':90},
                'end_to_end_ms':{'p50':60,'p95':95,'p99':100},
                'fallback_available_ms':{'p50':40,'p95':55},
                'average_encoded_mbps':1.2,'events_delivered':1,
                'outbox':{'pending':0}}
        row=summarize('Wi-Fi',report)
        self.assertEqual(row['remote_success'],9)
        with tempfile.TemporaryDirectory() as directory:
            outputs=write_outputs([row],Path(directory)/'comparison')
            self.assertTrue(all(Path(path).is_file() for path in outputs.values()))
            self.assertEqual(json.loads(Path(outputs['json']).read_text(encoding='utf-8'))
                             ['tests'][0]['label'],'Wi-Fi')

    def test_report_spec_requires_label(self):
        self.assertEqual(parse_spec('5G=result.json')[0],'5G')
        with self.assertRaises(ValueError):parse_spec('result.json')


if __name__=='__main__':unittest.main()
