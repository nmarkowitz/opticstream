import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from opticstream.config.migrate_psoct_blocks import migrate_blocks


def document(name, slug="psoctscanconfig"):
    return SimpleNamespace(name=name, block_type=SimpleNamespace(slug=slug), is_anonymous=False)


class MigrationTests(unittest.TestCase):
    def setup_mocks(self):
        client = Mock()
        client.read_block_documents.side_effect = [
            [document("a"), document("other", "secret")], [document("b")], [],
        ]
        cls = Mock()
        cls.get_block_type_slug.return_value = "psoctscanconfig"
        blocks = [Mock(), Mock()]
        cls.load.side_effect = blocks
        return client, cls, blocks

    def test_dry_run_paginated_and_type_filtered(self):
        client, cls, blocks = self.setup_mocks()
        self.assertEqual(migrate_blocks(client=client, block_class=cls), 0)
        self.assertEqual(cls.load.call_count, 2)
        cls.register_type_and_schema.assert_not_called()
        for block in blocks:
            block.save.assert_not_called()
        self.assertEqual(client.read_block_documents.call_args.kwargs["offset"], 3)

    def test_apply_preserves_loaded_objects(self):
        client, cls, blocks = self.setup_mocks()
        self.assertEqual(migrate_blocks(apply=True, client=client, block_class=cls), 0)
        cls.register_type_and_schema.assert_called_once()
        blocks[0].save.assert_called_once_with("a", overwrite=True)
        blocks[1].save.assert_called_once_with("b", overwrite=True)

    def test_invalid_aborts_all_writes(self):
        client, cls, blocks = self.setup_mocks()
        cls.load.side_effect = [blocks[0], ValueError("private")]
        self.assertEqual(migrate_blocks(apply=True, client=client, block_class=cls), 1)
        cls.register_type_and_schema.assert_not_called()
        blocks[0].save.assert_not_called()

    def test_partial_save_failure_stops(self):
        client, cls, blocks = self.setup_mocks()
        blocks[0].save.side_effect = RuntimeError("private")
        self.assertEqual(migrate_blocks(apply=True, client=client, block_class=cls), 1)
        blocks[1].save.assert_not_called()
