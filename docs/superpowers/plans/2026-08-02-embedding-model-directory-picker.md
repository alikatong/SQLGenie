# Embedding Model Directory Picker Implementation Plan

> Approved design: use a local native directory chooser instead of requiring administrators to type a model path.

## Goal

Let an administrator select a local Qwen Embedding model directory from the system file dialog. Keep the selected path read-only in the web form, preserve the existing path when the dialog is cancelled, and retain server-side Qwen/config.json validation.

## Changes

1. Add a backend picker helper that opens the native directory dialog, normalizes the selected path, and validates it as a Qwen model directory.
2. Add an admin-only API endpoint returning the selected path, cancellation state, and a clear error when the runtime cannot open a native dialog.
3. Replace the editable frontend path input with a read-only field and a `选择目录` action; disable it while saving or initializing RAG.
4. Add API and frontend regression coverage for selection, cancellation, and picker failure handling.

## Verification

- Run focused backend picker/API tests.
- Run frontend tests and production build.
- Run the full backend test suite.
- Confirm the commit contains no `.env`, database, Chroma, or model-weight files.
