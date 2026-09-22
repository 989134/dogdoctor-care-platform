import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from PIL import Image
from guardian.gemini import FIELDS, GeminiError, extract_label
from guardian.server import create_app


LABEL = dict(text='測試藥品 500mg', name='測試藥品', strength='500mg', license='',
             dosage_text='', expiry_text='', uncertainty='未拍到用法與效期')


class GeminiClientTests(unittest.TestCase):
    def response(self, value, reason='STOP'):
        return io.BytesIO(json.dumps({'candidates': [{'finishReason': reason,
            'content': {'parts': [{'text': json.dumps(value)}]}}]}).encode())

    def test_structured_request_and_image(self):
        with patch('guardian.gemini.urlopen', return_value=self.response(LABEL)) as call:
            self.assertEqual(extract_label('文字', b'png', 'secret', 'gemini-2.5-flash'), LABEL)
            req = call.call_args.args[0]
            self.assertNotIn('secret', req.full_url)
            self.assertEqual(req.get_header('X-goog-api-key'), 'secret')
            payload = json.loads(req.data)
            self.assertEqual(payload['contents'][0]['parts'][1]['inlineData']['data'], 'cG5n')
            self.assertEqual(call.call_args.kwargs['timeout'], 45)

    def test_malformed_blocked_or_incomplete_results(self):
        for value, reason in [(dict(LABEL, name=[]), 'STOP'), ({}, 'STOP'), (LABEL, 'MAX_TOKENS'), (LABEL, 'SAFETY')]:
            with self.subTest(value=value, reason=reason), patch('guardian.gemini.urlopen', return_value=self.response(value, reason)):
                with self.assertRaises(GeminiError): extract_label('文字', None, 'secret', 'model')

    def test_errors_are_sanitized(self):
        for error in [HTTPError('url', 429, 'secret upstream text', None, None), TimeoutError('secret')]:
            with patch('guardian.gemini.urlopen', side_effect=error):
                with self.assertRaises(GeminiError) as caught: extract_label('文字', None, 'secret', 'model')
                self.assertNotIn('secret', str(caught.exception))


class DrugApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.app = create_app(root/'data', root/'source')
        self.app.testing = True
        self.app.config['GEMINI_API_KEY'] = 'test-secret'
        self.addCleanup(self.app.extensions['guardian_db'].close)
        self.client = self.app.test_client()
        result = self.client.post('/api/login', json={'name': 'admin', 'password': 'test-password-long', 'setup': (root/'data/setup-token.txt').read_text()})
        self.headers = {'X-CSRF': result.json['csrf']}

    def send(self, **extra):
        return self.client.post('/api/drug', headers=self.headers,
            data=dict(text='測試藥品', provider='gemini', cloud_consent='true', **extra))

    def test_authorization_and_consent(self):
        self.assertEqual(self.app.test_client().get('/api/drug/config').status_code, 401)
        self.assertEqual(self.app.test_client().get('/api/drug/demo').status_code, 401)
        self.assertEqual(self.client.post('/api/drug', data={}).status_code, 403)
        with patch('guardian.gemini.extract_label') as extract:
            result = self.client.post('/api/drug', headers=self.headers, data={'text': 'test', 'provider': 'gemini'})
            self.assertEqual(result.status_code, 400)
            extract.assert_not_called()
        self.assertNotIn('test-secret', self.client.get('/api/drug/config').text)

    def test_demo_is_complete_and_does_not_call_gemini(self):
        with patch('guardian.gemini.extract_label') as extract:
            result = self.client.get('/api/drug/demo')
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json['demo'])
        self.assertEqual(result.json['provider'], 'demo')
        self.assertEqual(len(result.json['matches']), 1)
        self.assertEqual(result.json['image_url'], '/static/demo_panadol_500mg.jpg')
        for field in ('product_name','english_name','purpose_plain','indication','strength',
                      'form','ingredients','dosage_text','expiry_text','license',
                      'manufacturer','source','warning'):
            self.assertTrue(result.json['details'][field], field)
        self.assertIn('原藥袋', result.json['details']['warning'])
        extract.assert_not_called()

    def test_ai_candidate_remains_unverified_and_not_saved(self):
        with patch('guardian.gemini.extract_label', return_value=LABEL), patch('guardian.drug.drug_reader.Catalog') as catalog:
            catalog.return_value.search.return_value = []
            result = self.send()
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json['ai']['name'], LABEL['name'])
            self.assertEqual(result.json['matches'], [])
            self.assertTrue(result.json['needs_confirmation'])
            self.assertNotIn('500mg', result.json['details']['dosage_text'])
            self.assertEqual(self.client.get('/api/medicines').json['medicines'], [])

    def test_missing_key_and_upstream_error(self):
        self.app.config['GEMINI_API_KEY'] = ''
        self.assertEqual(self.send().status_code, 503)
        self.app.config['GEMINI_API_KEY'] = 'test-secret'
        with patch('guardian.gemini.extract_label', side_effect=GeminiError('連線失敗')):
            self.assertEqual(self.send().status_code, 502)

    def test_image_validation(self):
        with patch('guardian.gemini.extract_label') as extract:
            self.assertEqual(self.send(image=(io.BytesIO(b'not an image'), 'bad.png')).status_code, 400)
            extract.assert_not_called()

    def test_local_text_is_preserved_with_photo(self):
        buf = io.BytesIO(); Image.new('RGB', (10,10)).save(buf, 'PNG'); buf.seek(0)
        with patch('guardian.drug.drug_reader.Catalog') as catalog, patch('guardian.drug.drug_reader.recognize', return_value=('照片文字', None)):
            catalog.return_value.search.return_value=[]
            result=self.client.post('/api/drug', headers=self.headers, data={'provider':'local', 'text':'補充文字', 'image':(buf,'test.png')})
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json['text'], '補充文字\n照片文字')

    def test_rate_limit(self):
        with patch('guardian.gemini.extract_label', side_effect=GeminiError('busy')):
            for _ in range(6): self.assertEqual(self.send().status_code, 502)
            self.assertEqual(self.send().status_code, 429)


if __name__ == '__main__':
    unittest.main()
