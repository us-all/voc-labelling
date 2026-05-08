import unittest
from datetime import datetime, timezone

from src.bigquery.channel_queries import ChannelQueryService


class FakeBigQueryClient:
    project_id = "test-project"

    def __init__(self):
        self.queries = []

    def execute_query(self, query):
        self.queries.append(query)
        return []


class ChannelQueryServiceTest(unittest.TestCase):
    def test_kst_to_unix_ms_is_independent_of_local_timezone(self):
        ts = ChannelQueryService._kst_to_unix_ms("2026-04-27")

        self.assertEqual(
            datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat(),
            "2026-04-26T15:00:00+00:00",
        )

    def test_weekly_messages_defaults_to_us_plus_channel(self):
        client = FakeBigQueryClient()
        service = ChannelQueryService(client)

        service.get_weekly_messages("2026-05-06", "2026-05-07")

        self.assertIn("channelName = 'us-plus'", client.queries[0])

    def test_chat_states_defaults_to_us_plus_channel(self):
        client = FakeBigQueryClient()
        service = ChannelQueryService(client)

        service.get_chat_states(["chat-1"])

        self.assertIn("channelName = 'us-plus'", client.queries[0])

    def test_channel_name_can_be_overridden_for_future_campus_runs(self):
        client = FakeBigQueryClient()
        service = ChannelQueryService(client, channel_name="us-campus")

        service.get_weekly_messages("2026-05-06", "2026-05-07")

        self.assertIn("channelName = 'us-campus'", client.queries[0])


if __name__ == "__main__":
    unittest.main()
