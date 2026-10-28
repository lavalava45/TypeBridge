"""Retention/cap equivalence without format changes, encryption or editor reads."""
import random
import sqlite3
import unittest
from unittest.mock import patch

from input_journal import JournalStore


class PruningTests(unittest.TestCase):
    def connection(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute('CREATE TABLE snapshots(id INTEGER PRIMARY KEY, created REAL, payload BLOB)')
        return conn

    def test_same_newest_suffix_under_age_count_and_byte_caps(self):
        rng = random.Random(1487)
        conn = self.connection()
        for _ in range(100):
            conn.execute('DELETE FROM snapshots')
            rows = [(i, rng.randrange(5), bytes(rng.randrange(40)))
                    for i in range(1, rng.randrange(1, 80))]
            conn.executemany('INSERT INTO snapshots VALUES(?,?,?)', rows)
            max_count, max_bytes, cutoff = rng.randrange(1, 30), rng.randrange(1, 400), 2
            expected, size = [], 0
            for identifier, created, payload in reversed(rows):
                if created < cutoff:
                    continue
                size += len(payload)
                if len(expected) >= max_count or size > max_bytes:
                    break
                expected.append(identifier)
            with patch('input_journal.MAX_RECORDS', max_count), \
                 patch('input_journal.MAX_PAYLOAD_BYTES', max_bytes):
                JournalStore._prune(conn, cutoff)
            actual = [r[0] for r in conn.execute('SELECT id FROM snapshots ORDER BY id DESC')]
            self.assertEqual(actual, expected)

    def test_pruning_and_snapshot_rollback_together(self):
        conn = self.connection()
        conn.execute('INSERT INTO snapshots VALUES(1,1,?)', (b'old',))
        conn.commit()
        conn.execute('INSERT INTO snapshots VALUES(2,2,?)', (b'new',))
        with patch('input_journal.MAX_RECORDS', 1):
            JournalStore._prune(conn, 0)
        conn.rollback()
        self.assertEqual(conn.execute('SELECT id,payload FROM snapshots').fetchall(), [(1, b'old')])

    def test_other_writer_changes_are_included_without_stale_size_cache(self):
        conn = self.connection()
        conn.executemany('INSERT INTO snapshots VALUES(?,?,?)', [(1,1,b'a'),(2,2,b'b')])
        JournalStore._prune(conn, 0)
        conn.execute('UPDATE snapshots SET payload=? WHERE id=1', (b'x'*100,))
        with patch('input_journal.MAX_PAYLOAD_BYTES', 10):
            JournalStore._prune(conn, 0)
        self.assertEqual(conn.execute('SELECT id FROM snapshots').fetchall(), [(2,)])


if __name__ == '__main__':
    unittest.main()
