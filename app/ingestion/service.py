import hashlib
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.domain import Artifact, Document, DocumentRevision
from app.ingestion.artifacts import ArtifactStore
from app.platform.ids import new_id
from app.platform.repository import Repository

TRACKING_PARAMETERS = {"utm_source", "utm_medium", "utm_campaign", "spm"}


def canonicalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    parts = urlsplit(url.strip())
    query = urlencode(
        sorted(
            (key, value) for key, value in parse_qsl(parts.query) if key not in TRACKING_PARAMETERS
        )
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))


def normalize_text(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value.replace("\r\n", "\n").replace("\r", "\n")).strip()


class IngestionService:
    def __init__(self, repository: Repository, artifact_store: ArtifactStore) -> None:
        self.repository = repository
        self.artifact_store = artifact_store

    def ingest(
        self,
        *,
        source_id: str,
        source_tier: str,
        external_id: Optional[str],
        url: Optional[str],
        title: str,
        content: str,
        published_at: datetime,
    ) -> tuple[Document, str]:
        normalized_title = normalize_text(title)
        normalized_content = normalize_text(content)
        content_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
        raw_bytes = content.encode("utf-8")
        artifact_hash, artifact_uri = self.artifact_store.put(raw_bytes)
        _, normalized_uri = self.artifact_store.put(normalized_content.encode("utf-8"))
        artifact = self.repository.find_artifact(artifact_hash)
        if not artifact:
            artifact = Artifact(
                id=new_id("art"),
                sha256=artifact_hash,
                storage_uri=artifact_uri,
                mime_type="text/plain; charset=utf-8",
                byte_size=len(raw_bytes),
                created_at=datetime.now(timezone.utc),
            )
            self.repository.save_artifact(artifact)

        existing = self.repository.find_document(source_id, external_id, content_hash)
        if existing:
            if existing.content_hash != content_hash:
                latest = self.repository.get_latest_revision(existing.id)
                revision_no = latest.revision_no + 1 if latest else 1
                revised = replace(
                    existing,
                    source_tier=source_tier,
                    canonical_url=canonicalize_url(url),
                    title=normalized_title,
                    content=normalized_content,
                    content_hash=content_hash,
                    published_at=published_at,
                    ingested_at=datetime.now(timezone.utc),
                )
                self.repository.update_document(revised)
                self.repository.save_document_revision(
                    DocumentRevision(
                        id=new_id("rev"),
                        document_id=revised.id,
                        revision_no=revision_no,
                        artifact_id=artifact.id,
                        content_hash=content_hash,
                        normalized_content_uri=normalized_uri,
                        parser_version="inline-v1",
                        created_at=datetime.now(timezone.utc),
                    )
                )
                return revised, "revised"
            return existing, "duplicate"

        document = Document(
            id=new_id("doc"),
            source_id=source_id,
            source_tier=source_tier,
            external_id=external_id,
            canonical_url=canonicalize_url(url),
            title=normalized_title,
            content=normalized_content,
            content_hash=content_hash,
            published_at=published_at,
            ingested_at=datetime.now(timezone.utc),
        )
        self.repository.save_document(document)
        self.repository.save_document_revision(
            DocumentRevision(
                id=new_id("rev"),
                document_id=document.id,
                revision_no=1,
                artifact_id=artifact.id,
                content_hash=content_hash,
                normalized_content_uri=normalized_uri,
                parser_version="inline-v1",
                created_at=datetime.now(timezone.utc),
            )
        )
        return document, "created"
