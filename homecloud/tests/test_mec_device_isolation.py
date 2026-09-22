import unittest

from mec_service import DeviceRuntime
from care_engine import Settings


class Detector:
    pass


class MecDeviceIsolationTests(unittest.TestCase):
    def test_each_device_gets_independent_detector(self):
        first=DeviceRuntime(Settings(),Detector)
        second=DeviceRuntime(Settings(),Detector)
        self.assertIsNot(first.detector,second.detector)
        self.assertIsNot(first.care,second.care)


if __name__ == '__main__':
    unittest.main()
