import unittest

from src.classifier_v5.bedrock_classifier import (
    BedrockV5Classifier,
    MODEL_ID,
)


class BedrockV5CostReportTest(unittest.TestCase):
    def classifier_with_usage(self, model_id, input_tokens, output_tokens):
        classifier = BedrockV5Classifier.__new__(BedrockV5Classifier)
        classifier.model_id = model_id
        classifier._input_tokens = input_tokens
        classifier._output_tokens = output_tokens
        classifier._errors = 0
        classifier._total = 1
        return classifier

    def test_default_haiku_model_uses_haiku_pricing(self):
        classifier = self.classifier_with_usage(
            MODEL_ID,
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )

        self.assertEqual(classifier.get_cost_report()["cost_usd"], 6.0)

    def test_sonnet_model_uses_sonnet_pricing(self):
        classifier = self.classifier_with_usage(
            "us.anthropic.claude-sonnet-4-20250514-v1:0",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )

        self.assertEqual(classifier.get_cost_report()["cost_usd"], 18.0)


if __name__ == "__main__":
    unittest.main()
