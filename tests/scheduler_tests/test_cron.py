"""
Tests for cron expression parsing.
"""

# Path setup must happen before scheduler imports
import sys
from pathlib import Path
_this_file = Path(__file__).resolve()
_src_path = str(_this_file.parent.parent.parent / 'src')
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)
import os

from datetime import datetime
import pytest
import pytz

from scheduler.service import SchedulerService


class TestCronParsing:
    """Tests for cron expression parsing."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create a minimal service just for testing cron parsing
        self.service = SchedulerService.__new__(SchedulerService)

    def test_parse_standard_cron(self):
        """Test parsing standard cron expressions."""
        # Every day at 9:00 AM
        result = self.service._parse_cron("0 9 * * *")
        assert result == {
            'minute': '0',
            'hour': '9',
            'day': '*',
            'month': '*',
            'day_of_week': '*'
        }

    def test_parse_every_15_minutes(self):
        """Test parsing every 15 minutes cron."""
        result = self.service._parse_cron("*/15 * * * *")
        assert result == {
            'minute': '*/15',
            'hour': '*',
            'day': '*',
            'month': '*',
            'day_of_week': '*'
        }

    def test_parse_weekly_cron_sunday(self):
        """Test parsing weekly cron: Sunday (cron 0) maps to APScheduler 6."""
        result = self.service._parse_cron("0 0 * * 0")
        assert result == {
            'minute': '0',
            'hour': '0',
            'day': '*',
            'month': '*',
            'day_of_week': '6'  # cron 0=Sunday → APScheduler 6=Sunday
        }

    def test_parse_weekly_cron_monday(self):
        """Test parsing weekly cron: Monday (cron 1) maps to APScheduler 0."""
        result = self.service._parse_cron("5 9 * * 1")
        assert result == {
            'minute': '5',
            'hour': '9',
            'day': '*',
            'month': '*',
            'day_of_week': '0'  # cron 1=Monday → APScheduler 0=Monday
        }

    def test_parse_weekly_cron_sunday_alt(self):
        """Test parsing weekly cron: Sunday alternative (cron 7) maps to APScheduler 6."""
        result = self.service._parse_cron("0 0 * * 7")
        assert result == {
            'minute': '0',
            'hour': '0',
            'day': '*',
            'month': '*',
            'day_of_week': '6'  # cron 7=Sunday → APScheduler 6=Sunday
        }

    def test_parse_complex_cron(self):
        """Test parsing complex cron expression with range: left unchanged."""
        # 9:30 AM on weekdays — range expressions are not converted (complex case)
        result = self.service._parse_cron("30 9 * * 1-5")
        assert result == {
            'minute': '30',
            'hour': '9',
            'day': '*',
            'month': '*',
            'day_of_week': '1-5'  # ranges left unchanged
        }

    def test_parse_cron_day_wildcard_unchanged(self):
        """Test that wildcard day_of_week is not converted."""
        result = self.service._parse_cron("*/15 * * * *")
        assert result['day_of_week'] == '*'

    def test_parse_cron_day_step_unchanged(self):
        """Test that */n day_of_week step expressions are not converted."""
        result = self.service._parse_cron("0 9 * * */2")
        assert result['day_of_week'] == '*/2'

    def test_parse_invalid_cron_too_few_parts(self):
        """Test that invalid cron with too few parts raises error."""
        with pytest.raises(ValueError) as exc_info:
            self.service._parse_cron("0 9 * *")

        assert "Expected 5 parts" in str(exc_info.value)

    def test_parse_invalid_cron_too_many_parts(self):
        """Test that invalid cron with too many parts raises error."""
        with pytest.raises(ValueError) as exc_info:
            self.service._parse_cron("0 9 * * * *")

        assert "Expected 5 parts" in str(exc_info.value)

    def test_parse_cron_with_whitespace(self):
        """Test parsing cron with extra whitespace."""
        result = self.service._parse_cron("  0  9  *  *  *  ")
        assert result['minute'] == '0'
        assert result['hour'] == '9'


class TestCronDayOfWeekFiring:
    """Integration tests: verify CronTrigger fires on the correct day-of-week."""

    def setup_method(self):
        self.service = SchedulerService.__new__(SchedulerService)

    def test_monday_cron_fires_on_monday(self):
        """Cron '5 9 * * 1' (standard: Monday) must fire on Monday, not Tuesday."""
        from apscheduler.triggers.cron import CronTrigger
        import pytz
        tz = pytz.UTC
        # Saturday March 28, 2026
        now = datetime(2026, 3, 28, 10, 0, 0, tzinfo=tz)
        kwargs = self.service._parse_cron("5 9 * * 1")
        trigger = CronTrigger(timezone=tz, **kwargs)
        next_fire = trigger.get_next_fire_time(None, now)
        assert next_fire.strftime("%A") == "Monday", (
            f"Expected Monday, got {next_fire.strftime('%A %b %d')} — "
            "day_of_week numbering mismatch between cron and APScheduler"
        )

    def test_sunday_cron_fires_on_sunday(self):
        """Cron '0 10 * * 0' (standard: Sunday) must fire on Sunday, not Monday."""
        from apscheduler.triggers.cron import CronTrigger
        import pytz
        tz = pytz.UTC
        now = datetime(2026, 3, 28, 10, 0, 0, tzinfo=tz)  # Saturday
        kwargs = self.service._parse_cron("0 10 * * 0")
        trigger = CronTrigger(timezone=tz, **kwargs)
        next_fire = trigger.get_next_fire_time(None, now)
        assert next_fire.strftime("%A") == "Sunday", (
            f"Expected Sunday, got {next_fire.strftime('%A %b %d')}"
        )


class TestNextRunTime:
    """Tests for next run time calculation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = SchedulerService.__new__(SchedulerService)

    def test_next_run_utc(self):
        """Test next run time calculation in UTC."""
        next_run = self.service._get_next_run_time("0 9 * * *", "UTC")

        assert next_run is not None
        assert next_run.hour == 9
        assert next_run.minute == 0

    def test_next_run_with_timezone(self):
        """Test next run time calculation with timezone."""
        next_run = self.service._get_next_run_time("0 9 * * *", "America/New_York")

        assert next_run is not None
        # Verify timezone is applied
        tz = pytz.timezone("America/New_York")
        assert next_run.tzinfo is not None

    def test_next_run_every_15_minutes(self):
        """Test next run time for every 15 minutes."""
        next_run = self.service._get_next_run_time("*/15 * * * *", "UTC")

        assert next_run is not None
        assert next_run.minute in [0, 15, 30, 45]

    def test_next_run_invalid_expression(self):
        """Test next run time with invalid expression."""
        next_run = self.service._get_next_run_time("invalid cron", "UTC")
        assert next_run is None

    def test_next_run_invalid_timezone(self):
        """Test next run time with invalid timezone."""
        # Should fall back to UTC or return None
        next_run = self.service._get_next_run_time("0 9 * * *", "Invalid/Timezone")
        # Depending on implementation, this may return None or raise
        # In our implementation, it returns None on error
        assert next_run is None
