import unittest

from nodes.feed_publisher_node import _shared_source_key


class SourceDedupTest(unittest.TestCase):
    def test_jumpit_and_saramin_share_posting_id(self):
        jumpit = "https://jumpit.saramin.co.kr/position/55242853"
        saramin = "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=55242853"
        self.assertEqual(_shared_source_key(jumpit), _shared_source_key(saramin))
        self.assertNotEqual(_shared_source_key(jumpit), _shared_source_key(
            "https://jumpit.saramin.co.kr/position/55242888"))
        self.assertIsNone(_shared_source_key(
            "https://www.jobkorea.co.kr/Recruit/GI_Read/55242853"))


if __name__ == "__main__":
    unittest.main()
