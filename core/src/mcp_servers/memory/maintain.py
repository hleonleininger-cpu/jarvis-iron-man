"""Gedächtnispflege (M1.12): Dubletten entfernen, fehlende oder veraltete Embeddings nachholen,
Vault und Notizen abgleichen. Begrenzt, damit der Lauf auch beim Start nur kurz dauert."""

from dataclasses import dataclass
from pathlib import Path

from mcp_servers.memory.embed import Embedder, EmbedError
from mcp_servers.memory.store import MemoryStore
from mcp_servers.memory.vault import MAX_EMBEDS_PER_RUN, index_all


@dataclass(frozen=True)
class Maintenance:
    duplicates_removed: int
    memories_embedded: int
    memories_without_embedding: int
    vault: str

    def text(self) -> str:
        return (
            f"{self.duplicates_removed} Dubletten entfernt, {self.memories_embedded} Einträge "
            f"neu eingebettet, {self.memories_without_embedding} noch ohne Embedding. {self.vault}"
        )


def maintain(
    store: MemoryStore,
    roots: list[Path],
    notes_file: Path | None,
    embedder: Embedder | None,
    model: str,
) -> Maintenance:
    seen: set[str] = set()
    removed = 0
    for memory in store.entries():  # nach Nummer: der älteste Eintrag bleibt
        key = memory.text.casefold()
        if key in seen:
            store.delete(memory.id)
            removed += 1
        seen.add(key)
    embedded = 0
    if embedder is not None:
        pending = store.needing_embedding(model, MAX_EMBEDS_PER_RUN)
        try:
            vectors = embedder([m.text for m in pending]) if pending else []
        except EmbedError:
            vectors = []
        for memory, vector in zip(pending, vectors, strict=False):
            store.set_embedding(memory.id, vector, model)
            embedded += 1
    report = index_all(store, roots, notes_file, embedder, model)
    return Maintenance(
        removed,
        embedded,
        len(store.needing_embedding(model, 10**9)) if embedder else 0,
        report.text(),
    )
