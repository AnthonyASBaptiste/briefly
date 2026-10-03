# AGENTS.md

Guidance and standards for AI coding agents working on Briefly.

## Architecture Principles

1. **Stdlib-only**: Do not add third-party pip dependencies for core functionality. Keep Briefly lightweight and runnable with standard Python 3.10+.
2. **Local-first & Zero Data Egress**: Never send document contents or client metadata to cloud endpoints. Inference routes through local Ollama (`127.0.0.1:11434`).
3. **Safety by Default**: Never silently delete or overwrite files. Use duplicate timestamp suffixes on destination collision. Ambiguous files remain in the inbox for human review.

## Testing

Run tests with:
```bash
python3 -m unittest discover tests
```

- When adding new functionality, add unit tests in `tests/test_*.py`.
- Never commit code that breaks existing tests.
