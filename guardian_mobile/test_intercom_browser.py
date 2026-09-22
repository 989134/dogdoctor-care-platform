import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from guardian.server import create_app

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional browser validation dependency
    sync_playwright = None


CHROME = next((Path(path) for path in (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
) if Path(path).is_file()), None)


@unittest.skipUnless(sync_playwright and CHROME, "需要 Playwright 與本機 Chrome")
class IntercomBrowserTests(unittest.TestCase):
    """Creates two real browser contexts and verifies bidirectional audio RTP."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app(root / "data", root / "events")
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.setup_token = (root / "data" / "setup-token.txt").read_text().strip()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.app.extensions["guardian_db"].close()
        self.temp.cleanup()

    @staticmethod
    def _audio_bytes(page, link_name):
        return page.evaluate("""async name => {
            const link=name==='callLink'?callLink:vehicleLink;
            if(!link?.pc)return {sent:0,received:0,rows:[]};
            const stats=await link.pc.getStats();let sent=0,received=0,rows=[];
            stats.forEach(r=>{
              const kind=r.kind||r.mediaType;
              if(r.type==='outbound-rtp'&&kind==='audio')sent+=r.bytesSent||0;
              if(r.type==='inbound-rtp'&&kind==='audio')received+=r.bytesReceived||0;
              if(r.type==='outbound-rtp'||r.type==='inbound-rtp')rows.push({type:r.type,kind,bytesSent:r.bytesSent||0,bytesReceived:r.bytesReceived||0,packetsSent:r.packetsSent||0,packetsReceived:r.packetsReceived||0});
            });
            return {sent,received,rows};
        }""", link_name)

    def test_real_audio_rtp_mute_and_hangup(self):
        with sync_playwright() as play:
            browser = play.chromium.launch(
                executable_path=str(CHROME), headless=True,
                args=[
                    "--use-fake-ui-for-media-stream",
                    "--use-fake-device-for-media-stream",
                    "--autoplay-policy=no-user-gesture-required",
                    "--disable-features=WebRtcHideLocalIpsWithMdns",
                ],
            )
            caregiver_context = browser.new_context(permissions=["microphone"])
            vehicle_context = browser.new_context(permissions=["microphone"])
            caregiver = caregiver_context.new_page()
            vehicle = vehicle_context.new_page()
            caregiver.on("dialog", lambda dialog: dialog.accept())

            caregiver.goto(self.base)
            caregiver.locator("#loginButton").wait_for(state="visible")
            caregiver.fill("#name", "admin")
            caregiver.fill("#password", "browser-test-password")
            caregiver.fill("#confirmPassword", "browser-test-password")
            caregiver.fill("#setup", self.setup_token)
            caregiver.click("#loginButton")
            caregiver.locator("#portal").wait_for(state="visible")
            caregiver.click('[data-target="intercom"]')
            caregiver.locator("#vehiclePairing").evaluate("element => element.open=true")
            caregiver.click("#pairVehicle")
            caregiver.locator("#vehiclePairKey").wait_for(state="visible")
            pairing_key = caregiver.input_value("#vehiclePairKey")
            self.assertGreater(len(pairing_key), 30)

            vehicle.goto(self.base + "/vehicle")
            vehicle.fill("#vehicleKey", pairing_key)
            vehicle.click("#enableVehicle")
            vehicle.wait_for_function("vehicleEnabled === true")
            caregiver.wait_for_function("!document.querySelector('#startCall').disabled")

            caregiver.click("#startCall")
            caregiver.wait_for_function("callLink?.pc?.connectionState === 'connected'", timeout=30000)
            vehicle.wait_for_function("vehicleLink?.pc?.connectionState === 'connected'", timeout=30000)
            caregiver.wait_for_timeout(2500)

            caller_stats = self._audio_bytes(caregiver, "callLink")
            vehicle_stats = self._audio_bytes(vehicle, "vehicleLink")
            self.assertGreater(caller_stats["sent"], 0, caller_stats)
            self.assertGreater(caller_stats["received"], 0, caller_stats)
            self.assertGreater(vehicle_stats["sent"], 0, vehicle_stats)
            self.assertGreater(vehicle_stats["received"], 0, vehicle_stats)

            caregiver.evaluate("window.__callerTrack=callStream.getAudioTracks()[0]")
            caregiver.click("#muteCall")
            self.assertFalse(caregiver.evaluate("window.__callerTrack.enabled"))
            caregiver.click("#endCall")
            caregiver.wait_for_function("window.__callerTrack.readyState === 'ended'")

            vehicle.evaluate("window.__vehicleTrack=vehicleStream.getAudioTracks()[0]")
            vehicle.wait_for_function("vehicleCallId === null")
            self.assertFalse(vehicle.evaluate("window.__vehicleTrack.enabled"))
            vehicle.click("#disableVehicle")
            vehicle.wait_for_function("window.__vehicleTrack.readyState === 'ended'")

            caregiver_context.close()
            vehicle_context.close()
            browser.close()


if __name__ == "__main__":
    unittest.main()
