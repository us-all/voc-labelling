import unittest
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts import run_daily_pipeline


class RunDailyPipelineArgsTest(unittest.TestCase):
    def test_parser_supports_channel_only_backfill_aliases(self):
        parser = run_daily_pipeline.build_arg_parser()

        canonical = parser.parse_args(["--skip-letters-posts"])
        alias = parser.parse_args(["--skip-letters"])

        self.assertTrue(canonical.skip_letters_posts)
        self.assertTrue(alias.skip_letters_posts)

    def test_skip_letters_posts_does_not_delete_letters_posts_partition(self):
        args = SimpleNamespace(
            dry_run=False,
            skip_channel=True,
            skip_letters_posts=True,
            skip_slack=True,
            workers=1,
        )
        bq_client = MagicMock()
        bq_client.client = MagicMock()
        writer = MagicMock()
        writer.write_letters_posts.return_value = 0
        writer.write_channel_talk.return_value = 0

        with patch.object(run_daily_pipeline, "BigQueryClient", return_value=bq_client), \
            patch.object(run_daily_pipeline, "WeeklyDataQuery") as query_cls, \
            patch.object(run_daily_pipeline, "BedrockV5Classifier") as classifier_cls, \
            patch.object(run_daily_pipeline, "BigQueryWriter", return_value=writer):
            query_cls.return_value.get_master_info.return_value = {}
            query_cls.return_value.get_weekly_data.return_value = {"letters": [], "posts": []}

            run_daily_pipeline._run_pipeline(
                args,
                target_date="2026-05-06",
                next_date="2026-05-07",
                current_phase=["init"],
                pipeline_start=time.time(),
            )

        query_cls.return_value.get_master_info.assert_not_called()
        query_cls.return_value.get_weekly_data.assert_not_called()
        classifier_cls.assert_not_called()
        writer.write_letters_posts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
