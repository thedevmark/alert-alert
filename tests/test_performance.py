import sys
import os
import time
import unittest
from unittest.mock import patch, MagicMock

# Add parent directory to path to import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app

class TestDownloadPerformance(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    @patch('app.run_subprocess')
    def test_download_is_non_blocking(self, mock_run_subprocess):
        # Mock run_subprocess to sleep for 2 seconds to simulate download
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "yt-dlp" in str(cmd) or (len(cmd) > 0 and "yt-dlp" in str(cmd[0])):
                time.sleep(2)

            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
            return mock_result

        mock_run_subprocess.side_effect = side_effect

        with patch('pathlib.Path.glob') as mock_glob:
            # We need to return a list of files so the thread doesn't error out immediately after "download"
            # But here we just care about the initial response
            mock_file = MagicMock()
            mock_file.name = "clip.mp4"
            mock_glob.return_value = [mock_file]

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

if __name__ == '__main__':
    unittest.main()
