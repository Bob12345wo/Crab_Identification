import unittest

from onenet_mqtt_upload import build_request_id, build_topic


class OneNetMqttUploadTests(unittest.TestCase):
    def test_request_id_is_stable_numeric_and_at_most_13_digits(self):
        first = build_request_id("measurement-123", "fid-456")
        second = build_request_id("measurement-123", "fid-456")

        self.assertEqual(first, second)
        self.assertTrue(first.isdigit())
        self.assertLessEqual(len(first), 13)

    def test_property_reply_topic_matches_onenet_documentation(self):
        topic = build_topic(
            {
                "product_id": "product",
                "device_name": "device",
                "topic_mode": "property",
            }
        )

        self.assertEqual(topic, "$sys/product/device/thing/property/post")
        self.assertEqual(f"{topic}/reply", "$sys/product/device/thing/property/post/reply")


if __name__ == "__main__":
    unittest.main()
