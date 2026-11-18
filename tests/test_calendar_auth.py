"""Permiso de solo lectura de Google Calendar en el inicio de sesión (odd/tasks/clases-automaticas.md, S5)."""
import unittest

from capture.models import CaptureError
from capture.sources import auth
from tests.test_drive_user_credentials import READ, WRITE, Case

CALENDAR = "https://www.googleapis.com/auth/calendar.readonly"


class CalendarLoginTests(Case):
    def test_wizard_login_also_asks_for_read_only_calendar(self):
        self.credentials = self.make_credentials([READ, WRITE, CALENDAR])
        self.Flow.from_client_config.return_value.run_local_server.return_value = self.credentials
        self.login(write=True)
        self.assertEqual(self.Flow.from_client_config.call_args.kwargs["scopes"], [READ, WRITE, CALENDAR])
        self.Credentials.from_authorized_user_info.return_value = self.credentials
        with self.libraries():
            self.assertIs(auth.load_calendar_credentials(self.runtime), self.credentials)

    def test_calendar_is_optional_when_logging_in(self):
        self.credentials = self.make_credentials([READ, WRITE])  # desmarcó Calendar en Google
        self.Flow.from_client_config.return_value.run_local_server.return_value = self.credentials
        self.login(write=True)
        self.assertTrue(auth.write_token_path(self.runtime).is_file())
        self.Credentials.from_authorized_user_info.return_value = self.credentials
        with self.libraries(), self.assertRaises(CaptureError) as caught:
            auth.load_calendar_credentials(self.runtime)
        self.assertIn("Calendar", str(caught.exception))

    def test_without_a_login_there_is_no_calendar(self):
        with self.libraries():
            self.assertIsNone(auth.load_calendar_credentials(self.runtime))

    def test_refreshing_one_permission_keeps_the_others_stored(self):
        stored = self.make_credentials([READ, WRITE, CALENDAR])
        auth.store_token(auth.write_token_path(self.runtime), stored)
        stored.valid = False
        self.Credentials.from_authorized_user_info.return_value = stored
        with self.libraries():
            auth.load_write_credentials(self.runtime)
        self.assertEqual(self.Credentials.from_authorized_user_info.call_args.kwargs["scopes"], [READ, WRITE, CALENDAR])

    def test_console_login_still_asks_only_to_read_drive(self):
        self.credentials.granted_scopes = [READ]
        self.login()
        self.assertEqual(self.Flow.from_client_config.call_args.kwargs["scopes"], [READ])


if __name__ == "__main__":
    unittest.main()
