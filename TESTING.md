# Testing Briefly

Briefly adheres to a zero-dependency philosophy using Python's standard library. Tests are implemented with Python's built-in `unittest` framework and require no external package installations.

## Running Tests

Run the full automated test suite:

```bash
python3 -m unittest discover tests
```

Or run the test file directly:

```bash
python3 -m unittest tests/test_briefly.py
```

## Test Coverage & Layers

- **Unit Tests**:
  - Safe path resolution and expansion (`test_safe_path`)
  - Document text extraction for TXT, Markdown, and DOCX (`test_extract_text_*`)
  - Unsupported file format safety checks
  - Matter folder enumeration and sanitized creation (`test_create_matter_folder`)
  - Model classification JSON null handling (`test_classify_null_handling`)
  - Confident matter matching logic (`test_classify_match_existing_matter`)
  - Collision-safe filing with duplicate-destination timestamp suffixes (`test_duplicate_destination_protection`)

## Conventions

- Tests are located in `tests/test_*.py`.
- Tests use standard library `tempfile` directories for file system isolation.
- External network and LLM calls (e.g., Ollama endpoints) are mocked to ensure deterministic, fast, offline test execution.
