import sys
import os
import time
import unittest
from unittest.mock import patch

# Add parent directory to path to import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app

class TestDownloadPerformance(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    @patch('alert.threading.Thread.start', autospec=True)
    def test_download_is_non_blocking(self, mock_thread_start):
        # The route should return immediately after scheduling work on a background thread.
        mock_thread_start.return_value = None

        with patch('uuid.uuid4') as mock_uuid:
            mock_uuid.return_value.hex = "12345678deadbeef"
            start_time = time.time()
            response = self.client.post('/api/download', json={
                "url": "https://www.youtube.com/watch?v=12345",
                "start": "00:00:00",
                "end": "00:00:10"
            })
            end_time = time.time()

            duration = end_time - start_time
            print(f"Request took {duration:.4f} seconds")

            # Assert that the request took LESS than 0.5 seconds (non-blocking)
            self.assertTrue(duration < 0.5, f"Request took too long: {duration}s")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data['status'], 'downloading')
            self.assertIn('job_id', data)
            mock_thread_start.assert_called_once()

if __name__ == '__main__':
    unittest.main()
