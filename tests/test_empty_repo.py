"""Empty repository 409 must not abort the weekly audit."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch
from tools import due_backfill as db


def conflict(message, code=409):
    return urllib.error.HTTPError('https://api.github.com/test', code, 'error', {},
                                  io.BytesIO(json.dumps({'message': message}).encode()))


class EmptyRepo(unittest.TestCase):
    def test_confirmed_empty_repo_returns_no_commits(self):
        with patch.object(db, 'list_paginated', side_effect=conflict('Git Repository is empty.')):
            self.assertEqual(db.repo_commits('token', 'empty', '2026-10-15'), [])

    def test_other_conflict_not_hidden(self):
        with patch.object(db, 'list_paginated', side_effect=conflict('Other conflict')):
            with self.assertRaises(urllib.error.HTTPError):
                db.repo_commits('token', 'repo', '2026-10-15')

    def test_permission_error_not_hidden(self):
        with patch.object(db, 'list_paginated', side_effect=conflict('Forbidden', 403)):
            with self.assertRaises(urllib.error.HTTPError):
                db.repo_commits('token', 'repo', '2026-10-15')

    def test_normal_commits_preserved(self):
        rows = [{'sha': 'abc', 'commit': {'message': 'hi'}}]
        with patch.object(db, 'list_paginated', return_value=rows):
            self.assertEqual(db.repo_commits('token', 'repo', '2026-10-15'), rows)
