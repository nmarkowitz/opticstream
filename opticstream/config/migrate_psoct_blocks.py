"""Refresh saved PSOCT schemas: python -m opticstream.config.migrate_psoct_blocks."""

import argparse

from prefect.client.orchestration import get_client

from opticstream.config.psoct_scan_config import PSOCTScanConfig


def migrate_blocks(*, apply=False, client=None, block_class=PSOCTScanConfig):
    """Validate all named blocks before writing; never print block values/secrets.

    This is an additive-schema migration, not a transactional server operation.
    Pause block editing while applying; saves overwrite each document individually.
    """
    if client is None:
        with get_client(sync_client=True) as active_client:
            return migrate_blocks(apply=apply, client=active_client, block_class=block_class)

    names = set()
    offset = 0
    while True:
        documents = client.read_block_documents(
            offset=offset, limit=100, include_secrets=False,
        )
        if not documents:
            break
        for document in documents:
            if (document.block_type.slug == block_class.get_block_type_slug()
                    and document.name and not document.is_anonymous):
                names.add(document.name)
        offset += len(documents)

    blocks = []
    failed = False
    for name in sorted(names):
        try:
            # Load normally to retain nested Secret references and apply defaults.
            block = block_class.load(name)
        except Exception as exc:
            # Exception messages from validation can include secret input values.
            print(f"INVALID: {name} ({type(exc).__name__}); inspect this block privately.")
            failed = True
        else:
            blocks.append((name, block))
            print(f"VALID: {name}")
    if failed:
        print("Aborted: no blocks saved. Correct invalid blocks and retry.")
        return 1
    if not apply:
        print(f"Dry run: {len(blocks)} block(s) validated. Use --apply to update schemas.")
        return 0
    if not blocks:
        print("No matching named blocks found.")
        return 0

    block_class.register_type_and_schema()
    for index, (name, block) in enumerate(blocks):
        try:
            block.save(name, overwrite=True)
        except Exception as exc:
            print(f"SAVE FAILED: {name} ({type(exc).__name__}). "
                  f"{index} earlier block(s) updated; remaining blocks not attempted. Retry after fixing.")
            return 1
        print(f"UPDATED: {name}")
    print(f"Updated {len(blocks)} block(s). Refresh the dashboard and restart consumers.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Save all validated blocks; default is dry-run.")
    args = parser.parse_args()
    return migrate_blocks(apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
