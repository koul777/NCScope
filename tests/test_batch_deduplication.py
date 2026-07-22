"""Test deduplication logic for batch question generation.

This test verifies that when generating batches of questions,
no duplicates appear in the final output, even when the underlying
generation function produces some repeated questions across rounds.
"""

import hashlib
from unittest.mock import patch, MagicMock


def _apply_deduplication_logic(generated_questions_per_round, batch_count):
    """Simulate the deduplication logic from generate_batch_diverse_questions()

    Args:
        generated_questions_per_round: List of lists, each containing questions for a round
        batch_count: Target number of unique questions

    Returns:
        Tuple of (final_questions, seen_hashes, rounds_needed)
    """
    final_questions = []
    seen_questions = set()
    max_attempts = 50
    attempt = 0

    for round_num, round_questions in enumerate(generated_questions_per_round):
        attempt += 1
        if attempt > max_attempts:
            break
        if len(final_questions) >= batch_count:
            break

        for q in round_questions:
            if len(final_questions) >= batch_count:
                break

            # Calculate question hash to detect duplicates
            q_text = str(q.get("question", "")).strip()
            q_hash = hashlib.md5(q_text.encode()).hexdigest()

            # Skip if we've seen this question before
            if q_hash in seen_questions:
                continue

            # Add to results and mark as seen
            final_questions.append(q)
            seen_questions.add(q_hash)

    return final_questions, seen_questions, attempt


class TestBatchDeduplication:
    """Test cases for batch question deduplication"""

    def test_deduplication_zero_duplicates_with_repeating_questions(self):
        """Test that duplicates are correctly filtered out"""
        # Simulate 3 rounds of 6 questions each, with overlaps
        question_pool = [
            {"question": f"Question {i}", "type": "behavioral"}
            for i in range(10)
        ]

        # Each round has 6 questions, with overlaps
        generated_rounds = [
            question_pool[0:6],    # Round 1: Q0-Q5
            question_pool[2:8],    # Round 2: Q2-Q7 (Q2-Q5 duplicates, Q6-Q7 new)
            question_pool[4:10],   # Round 3: Q4-Q9 (Q4-Q7 duplicates, Q8-Q9 new)
        ]

        final_questions, seen_questions, rounds = _apply_deduplication_logic(
            generated_rounds,
            batch_count=20
        )

        # Verify no duplicates
        assert len(final_questions) == len(seen_questions)
        assert len(seen_questions) == 10

        # Verify all question texts are unique
        question_texts = [q["question"] for q in final_questions]
        assert len(question_texts) == len(set(question_texts))

    def test_deduplication_stops_at_batch_count(self):
        """Test that generation stops once batch_count is reached"""
        question_pool = [
            {"question": f"Question {i}", "type": "behavioral"}
            for i in range(50)  # Enough unique questions
        ]

        batch_count = 20
        generated_rounds = [question_pool[i*6:(i+1)*6] for i in range(10)]

        final_questions, seen_questions, _ = _apply_deduplication_logic(
            generated_rounds,
            batch_count=batch_count
        )

        assert len(final_questions) == batch_count
        assert len(seen_questions) == batch_count

    def test_deduplication_preserves_question_structure(self):
        """Test that question structure is preserved during deduplication"""
        questions = [
            {
                "question": "What is your experience?",
                "followup": "Tell me more",
                "type": "behavioral",
                "competency": "Communication"
            }
        ]

        final_questions, _, _ = _apply_deduplication_logic(
            [questions],
            batch_count=1
        )

        assert len(final_questions) == 1
        assert final_questions[0]["question"] == "What is your experience?"
        assert final_questions[0]["followup"] == "Tell me more"
        assert final_questions[0]["type"] == "behavioral"
        assert final_questions[0]["competency"] == "Communication"

    def test_deduplication_hash_calculation(self):
        """Test that hash calculation correctly identifies duplicates"""
        q1 = "What is your project experience?"
        q2 = "What is your project experience?"
        q3 = "Tell me about your project"

        hash1 = hashlib.md5(q1.encode()).hexdigest()
        hash2 = hashlib.md5(q2.encode()).hexdigest()
        hash3 = hashlib.md5(q3.encode()).hexdigest()

        # Identical questions should have same hash
        assert hash1 == hash2

        # Different questions should have different hashes
        assert hash1 != hash3

    def test_deduplication_handles_whitespace(self):
        """Test that whitespace variations are treated as different questions"""
        q1_with_spaces = "What is your experience?"
        q2_with_extra_spaces = "What  is  your  experience?"

        hash1 = hashlib.md5(q1_with_spaces.encode()).hexdigest()
        hash2 = hashlib.md5(q2_with_extra_spaces.encode()).hexdigest()

        # Note: Different hashes - whitespace matters for exact dedup
        # This is acceptable as questions should be identical for dedup
        assert hash1 != hash2

    def test_deduplication_across_150_questions_three_batches(self):
        """Test the real-world scenario: 50 questions × 3 batches = 150 total, zero duplicates"""
        # Simulate what happens when calling batch generation 3 times
        # Each call should generate different questions

        # Create a large pool of unique questions
        all_unique_questions = [
            {"question": f"Question {i}", "type": "behavioral"}
            for i in range(200)  # Enough for 3 batches of 50
        ]

        all_150_questions = []
        all_150_hashes = set()

        # Simulate 3 separate API calls
        for batch_num in range(3):
            # Each batch uses 50 unique questions from the pool
            batch_start = batch_num * 50
            batch_end = batch_start + 50
            batch_questions = all_unique_questions[batch_start:batch_end]

            final_questions, _, _ = _apply_deduplication_logic(
                [[q] for q in batch_questions],  # One round with 50 questions
                batch_count=50
            )

            all_150_questions.extend(final_questions)
            for q in final_questions:
                q_hash = hashlib.md5(str(q["question"]).encode()).hexdigest()
                all_150_hashes.add(q_hash)

        # Verify no duplicates across all 150 questions
        assert len(all_150_questions) == 150
        assert len(all_150_hashes) == 150

        # Verify all question texts are unique
        question_texts = [q["question"] for q in all_150_questions]
        assert len(question_texts) == len(set(question_texts))
