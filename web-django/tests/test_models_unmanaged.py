"""Invariant (c): every model of the `social` app is unmanaged.

The project adopts an existing database. A managed model would let
`manage.py migrate` issue DDL against `social`, which `schema.sql` and
`soc migrate` own. The table set is pinned alongside it, so a model silently
losing its `db_table` is caught too.
"""

import json
import unittest

from ._probe import DUMMY_DATABASE_URL, DUMMY_SECRET, run_probe

READ_MODEL_META = """
import json
import django
django.setup()
from django.apps import apps
print(json.dumps({
    m.__name__: {'managed': m._meta.managed, 'table': m._meta.db_table}
    for m in apps.get_app_config('social').get_models()
}))
"""

EXPECTED_TABLES = {
    'People': 'people',
    'Drafts': 'drafts',
    'Interactions': 'interactions',
    'Questions': 'questions',
    'Ledger': 'ledger',
    'Leads': 'leads',
}


class ModelsAreUnmanagedTest(unittest.TestCase):
    def setUp(self):
        result = run_probe(
            READ_MODEL_META, DATABASE_URL=DUMMY_DATABASE_URL, SECRET_KEY=DUMMY_SECRET
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.models = json.loads(result.stdout)

    def test_every_model_is_unmanaged(self):
        managed = [name for name, meta in self.models.items() if meta['managed']]
        self.assertEqual(managed, [], f'managed models would let migrate alter social: {managed}')

    def test_the_six_existing_tables_are_mapped(self):
        self.assertEqual(
            {name: meta['table'] for name, meta in self.models.items()},
            EXPECTED_TABLES,
        )


if __name__ == '__main__':
    unittest.main()
