"""Twilio SMS transport. A queued request is not a delivery confirmation."""
import base64
import json
import os
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SmsTransport:
    def __init__(self):
        self.sid=os.environ.get('GUARDIAN_TWILIO_SID','').strip()
        self.token=os.environ.get('GUARDIAN_TWILIO_TOKEN','').strip()
        self.sender=os.environ.get('GUARDIAN_SMS_FROM','').strip()
        self.ready=bool(re.fullmatch(r'AC[0-9a-fA-F]{32}',self.sid) and self.token and re.fullmatch(r'\+[1-9][0-9]{7,14}',self.sender))

    def request(self, message_id=None, phone=None, body=None):
        path='Messages'
        if message_id:
            if not re.fullmatch(r'SM[0-9a-fA-F]{32}',message_id):raise ValueError('Invalid SMS receipt')
            path+='/'+message_id
        url=f'https://api.twilio.com/2010-04-01/Accounts/{self.sid}/{path}.json'
        headers={'Authorization':'Basic '+base64.b64encode((self.sid+':'+self.token).encode()).decode()}
        payload=None
        if phone:
            payload=urlencode({'To':phone,'From':self.sender,'Body':body,'ValidityPeriod':600}).encode()
            headers['Content-Type']='application/x-www-form-urlencoded'
        with urlopen(Request(url,data=payload,headers=headers),timeout=10) as response:
            data=json.load(response)
        if not isinstance(data,dict) or not re.fullmatch(r'SM[0-9a-fA-F]{32}',str(data.get('sid',''))):raise ValueError('Invalid SMS response')
        return data['sid'],data.get('status','unknown')
