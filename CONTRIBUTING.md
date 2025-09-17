# Contributing to HPXML Schema API

Thank you for your interest in contributing to the HPXML Schema API!

## Development Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/canmet-energy/hpxml-schema-api.git
   cd hpxml-schema-api
   ```

2. **Install in development mode**:
   ```bash
   pip install -e ".[dev]"
   ```

3. **Run tests**:
   ```bash
   pytest tests/ -v
   ```

## Code Guidelines

- **Follow PEP 8**: Use `black` for formatting and `ruff` for linting
- **Type hints**: Add type hints for new functions and classes
- **Documentation**: Include docstrings for public functions and classes
- **Tests**: Add tests for new functionality

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test categories
pytest tests/test_xsd_parser.py -v
pytest tests/test_app.py -v

# Run with coverage
pytest tests/ --cov=hpxml_schema_api
```

## Code Style

```bash
# Format code
black src/ tests/

# Check linting
ruff check src/ tests/

# Type checking
mypy src/
```

## Submitting Changes

1. **Fork the repository** on GitHub
2. **Create a feature branch** from `main`
3. **Make your changes** with tests and documentation
4. **Run the full test suite** to ensure nothing is broken
5. **Submit a pull request** with a clear description

## Pull Request Guidelines

- **Clear description**: Explain what the PR does and why
- **Tests**: Include tests for new functionality
- **Documentation**: Update README or docstrings as needed
- **Clean commits**: Use clear commit messages
- **Single purpose**: One feature or fix per PR

## Reporting Issues

When reporting bugs or requesting features:

- **Search existing issues** first to avoid duplicates
- **Provide clear description** of the problem or feature request
- **Include code examples** when reporting bugs
- **Specify environment details** (Python version, OS, etc.)

## Questions?

Feel free to open an issue for questions about:
- HPXML schema interpretation
- API design decisions
- Development setup problems
- Feature implementation guidance

We appreciate your contributions to making HPXML more accessible!