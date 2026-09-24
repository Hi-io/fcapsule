import unittest

from fcapsule.episode_investigation import EVIDENCE_REVIEW_SYSTEM


class EpisodeReviewPromptContractTests(unittest.TestCase):
    def test_evidence_review_requires_a_resource_scoped_discriminating_mechanism(self):
        for requirement in (
            "Reject an alert-name or symptom restatement as a mechanism",
            "every retained causal step, tie it to cited",
            "supplied affected resource",
            "incident/capture-time window",
            "exact missing discriminator",
            "specific resource/time-scoped next check",
            "would support or weaken the mechanism",
            "Generic log/health checks and configuration presence are not discriminators",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, EVIDENCE_REVIEW_SYSTEM)

    def test_review_stays_with_supplied_observations_without_tools_or_new_evidence(self):
        self.assertIn("using only the supplied observations", EVIDENCE_REVIEW_SYSTEM)
        self.assertIn("do not invent evidence", EVIDENCE_REVIEW_SYSTEM)
        self.assertIn("No tools or remediation", EVIDENCE_REVIEW_SYSTEM)


if __name__ == "__main__":
    unittest.main()
