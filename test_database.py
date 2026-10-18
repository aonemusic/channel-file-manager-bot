import tempfile
import unittest
from pathlib import Path

from database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tempdir.name) / "test.sqlite3"))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_channel_registration_and_status(self):
        self.db.upsert_channel(-100123, "Example", "example", "administrator")
        row = self.db.get_channel(-100123)
        self.assertEqual(row["title"], "Example")
        self.assertTrue(row["is_active"])
        self.db.update_channel_status(-100123, "left", False)
        self.assertFalse(self.db.get_channel(-100123)["is_active"])

    def test_candidate_batches_and_forward_deduplication(self):
        self.db.create_plan("plan-1", 1234, -100123, -100456)
        self.db.add_candidates("plan-1", [10, 20, 30, 40, 50])
        self.assertEqual(
            list(self.db.candidate_batches("plan-1", 2)),
            [[10, 20], [30, 40], [50]],
        )
        self.assertFalse(self.db.already_forwarded(-100123, -100456, 10))
        self.db.record_forward(-100123, -100456, 10)
        self.db.record_forward(-100123, -100456, 10)
        self.assertTrue(self.db.already_forwarded(-100123, -100456, 10))


if __name__ == "__main__":
    unittest.main()
