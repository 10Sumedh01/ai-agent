# Project Change Log

This file records meaningful project changes, why they were made, and any code
that was removed. Add new entries at the top and state `None` when a change
removes no code. Git history remains the source for exact line-by-line diffs.

## 2026-11-26 — Document cleanup and indexing retries

**What changed**

- Added cleanup for partial document files when storage or database persistence fails.
- Kept uploaded files and failed document records when indexing fails, with an
  owner-only `POST /documents/{document_id}/retry` endpoint to retry indexing.
- Made deletion failures report an explicit retryable error so clients can repeat
  the delete request to finish cleanup.
- Added tests for owner-only indexing retries and deletion retries after vector
  cleanup failures.

**Why**

Document storage, database records, and vector indexes are separate resources
that can fail independently. Cleanup and retry paths make these failures
recoverable without losing a valid upload.

**Code removed**

None. Existing upload and delete flows were extended with cleanup and retry
handling.

## 2026-11-25 — Document search and RAG flow

**What changed**

- Added authenticated endpoints to upload, list, and delete PDF and TXT documents.
- Added local file storage, a 10 MiB upload limit, text extraction, and chunking.
- Added CPU-based sentence embeddings and Qdrant indexing, scoped to each user.
- Added document retrieval to normal and streaming chat prompts.
- Saved retrieved source metadata with assistant messages.
- Added tests for document upload, ownership, deletion, and chat context.

**Why**

The project had document metadata and a source field, but no working path from
an uploaded document to a grounded chat response. This connects those pieces
while keeping the first implementation local and synchronous for ease of
learning and development.

**Code removed**

None. New endpoints and services were added, and existing chat paths were
extended to include retrieved context.

## 2026-11-25 — Pulled upstream RAG groundwork

**What changed**

- Added the `documents` table and `messages.sources` JSONB column through an
  Alembic migration.
- Added a `Document` model and relationships from users to documents.
- Introduced `UUIDPKMixin` and `TimestampMixin` for model fields shared by
  multiple models.
- Removed the root `requirements.txt`; `backend/requirements.txt` remains the
  backend dependency manifest.

**Why**

The document table and message source field provide persistence for document
ingestion and citations. The mixins avoid repeating UUID and timestamp column
definitions. The root requirements file was removed in the upstream update;
its exact rationale was not available in the repository history reviewed here.

**Code removed**

Repeated UUID and `created_at` field declarations were replaced by shared
mixins. The root dependency file was removed upstream; the backend-specific
dependency file remains.

## 2026-11-23 — Project README

**What changed**

Added a project README covering architecture, branch history, local setup, API
examples, tests, and a suggested learning path.

**Why**

The repository had no project documentation, so a new contributor lacked a
guided way to run the backend and learn its design through the branch history.

**Code removed**

None. This was a documentation-only change.
