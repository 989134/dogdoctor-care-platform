"""Production WSGI entry point for the MEC container/VM."""
import json
import os
from pathlib import Path

from mec_service import create_app
from eldertest.care_engine import Settings


root=Path(__file__).resolve().parent
settings_path=Path(os.getenv('CARE_SETTINGS_PATH',root/'care_settings.json'))
settings=Settings(**json.loads(settings_path.read_text(encoding='utf-8')))
default_store=(Path('/data/mec_events.sqlite3') if Path('/data').is_dir()
               else root/'mec_output'/'mec_events.sqlite3')
app=create_app(settings=settings,
               api_token=os.getenv('MEC_API_TOKEN',''),
               event_store_path=os.getenv('MEC_EVENT_STORE',str(default_store)))
